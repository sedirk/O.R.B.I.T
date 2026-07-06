import hashlib
import os
import re
from datetime import datetime
from typing import Iterable, Optional

import cv2
import requests


class HomeboxError(RuntimeError):
    pass


class HomeboxClient:
    """Small Homebox API client.

    Homebox exposes its API below /api/v1. v0.24 used item/location endpoints;
    v0.26 moved inventory objects to entity endpoints. This client keeps both
    shapes behind the same methods.
    """

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v1"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

        if token:
            self.set_token(token)
        elif username and password:
            self.login(username, password)

    @classmethod
    def from_env(cls, base_url: Optional[str] = None) -> "HomeboxClient":
        return cls(
            base_url or os.getenv("HOMEBOX_URL", "http://192.168.31.3:3100"),
            token=os.getenv("HOMEBOX_TOKEN"),
            username=os.getenv("HOMEBOX_USERNAME"),
            password=os.getenv("HOMEBOX_PASSWORD"),
        )

    @property
    def authenticated(self) -> bool:
        return "Authorization" in self.session.headers

    def set_token(self, token: str) -> None:
        value = str(token or "").strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        self.session.headers.update({"Authorization": value})

    def login(self, username: str, password: str) -> str:
        payload = {"username": username, "password": password, "stayLoggedIn": True}
        response = self.session.post(
            f"{self.api_url}/users/login", json=payload, timeout=self.timeout
        )
        self._raise_for_status(response, "Homebox login failed")
        token = response.json().get("token")
        if not token:
            raise HomeboxError("Homebox login did not return a token")
        self.set_token(token)
        return token

    def health(self) -> dict:
        response = requests.get(f"{self.api_url}/status", timeout=self.timeout)
        self._raise_for_status(response, "Homebox health check failed")
        return response.json()

    def get_locations(self) -> list[dict]:
        if not self.authenticated:
            return []
        response = self.session.get(
            f"{self.api_url}/locations",
            params={"filterChildren": "false"},
            timeout=self.timeout,
        )
        if response.status_code != 404:
            self._raise_for_status(response, "Homebox locations query failed")
            return response.json()

        response = self.session.get(f"{self.api_url}/entities/tree", timeout=self.timeout)
        self._raise_for_status(response, "Homebox location tree query failed")
        return self._flatten_location_tree(response.json())

    def get_tags(self) -> list[dict]:
        if not self.authenticated:
            return []
        response = self.session.get(f"{self.api_url}/tags", timeout=self.timeout)
        self._raise_for_status(response, "Homebox tags query failed")
        return response.json()

    def list_items(self, q: Optional[str] = None, page: int = 1, page_size: int = 20) -> dict:
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; cannot query items")
        params = {"page": page, "pageSize": page_size}
        if q:
            params["q"] = q
        response = self.session.get(f"{self.api_url}/items", params=params, timeout=self.timeout)
        if response.status_code == 404:
            response = self.session.get(f"{self.api_url}/entities", params=params, timeout=self.timeout)
        self._raise_for_status(response, "Homebox items query failed")
        return response.json()

    def get_item(self, item_id: str) -> dict:
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; cannot read item")
        response = self.session.get(f"{self.api_url}/items/{item_id}", timeout=self.timeout)
        if response.status_code == 404:
            response = self.session.get(f"{self.api_url}/entities/{item_id}", timeout=self.timeout)
        self._raise_for_status(response, "Homebox item read failed")
        return response.json()

    def delete_item(self, item_id: str) -> bool:
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; cannot delete item")
        response = self.session.delete(f"{self.api_url}/items/{item_id}", timeout=self.timeout)
        if response.status_code == 404:
            response = self.session.delete(f"{self.api_url}/entities/{item_id}", timeout=self.timeout)
        self._raise_for_status(response, "Homebox item deletion failed")
        return response.status_code in (200, 202, 204)

    def set_item_asset_id(self, item_id: str, asset_id: str) -> dict:
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; cannot update item")
        asset_id = self._normalize_asset_id(asset_id)
        item = self.get_item(item_id)
        tag_ids = []
        for tag in item.get("tags") or item.get("labels") or []:
            if isinstance(tag, dict) and tag.get("id"):
                tag_ids.append(tag["id"])
        payload = {
            "id": item_id,
            "name": item.get("name") or "未命名物品",
            "assetId": asset_id,
            "description": item.get("description") or "",
            "quantity": item.get("quantity") or 1,
            "tagIds": tag_ids,
            "manufacturer": str(item.get("manufacturer") or "").strip(),
            "modelNumber": str(item.get("modelNumber") or item.get("model") or "").strip(),
            "fields": self._without_orbit_code_field(item.get("fields") or []),
            "notes": item.get("notes") or "",
        }
        entity_type = item.get("entityType") if isinstance(item.get("entityType"), dict) else {}
        if entity_type.get("id"):
            payload["entityTypeId"] = entity_type["id"]
        location_id = (
            item.get("locationId")
            or item.get("parentId")
            or ((item.get("location") or {}).get("id") if isinstance(item.get("location"), dict) else None)
            or ((item.get("parent") or {}).get("id") if isinstance(item.get("parent"), dict) else None)
        )
        item_payload = dict(payload)
        entity_payload = dict(payload)
        if location_id:
            item_payload["locationId"] = location_id
            entity_payload["parentId"] = location_id

        response = self.session.put(f"{self.api_url}/items/{item_id}", json=item_payload, timeout=self.timeout)
        if response.status_code == 404:
            response = self.session.put(f"{self.api_url}/entities/{item_id}", json=entity_payload, timeout=self.timeout)
        self._raise_for_status(response, "Homebox asset ID update failed")
        updated = response.json() if response.content else dict(item)
        if isinstance(updated, dict):
            updated["assetId"] = asset_id
        return updated

    def create_tag(self, name: str, color: str = "#33D17A", description: str = "") -> dict:
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; cannot create tag")
        payload = {"name": name.strip(), "color": color, "description": description}
        response = self.session.post(f"{self.api_url}/tags", json=payload, timeout=self.timeout)
        self._raise_for_status(response, f"Homebox tag creation failed for {name}")
        return response.json()

    def delete_tag(self, tag_id: str) -> bool:
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; cannot delete tag")
        response = self.session.delete(f"{self.api_url}/tags/{tag_id}", timeout=self.timeout)
        self._raise_for_status(response, "Homebox tag deletion failed")
        return response.status_code in (200, 202, 204)

    def create_location(self, name: str, description: str = "", parent_id: Optional[str] = None) -> dict:
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; cannot create location")
        payload = {"name": name.strip(), "description": description}
        if parent_id:
            payload["parentId"] = parent_id
        response = self.session.post(f"{self.api_url}/locations", json=payload, timeout=self.timeout)
        if response.status_code == 404:
            entity_payload = dict(payload)
            location_type_id = self._location_entity_type_id()
            if location_type_id:
                entity_payload["entityTypeId"] = location_type_id
            response = self.session.post(f"{self.api_url}/entities", json=entity_payload, timeout=self.timeout)
        self._raise_for_status(response, f"Homebox location creation failed for {name}")
        return response.json()

    def delete_location(self, location_id: str) -> bool:
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; cannot delete location")
        response = self.session.delete(f"{self.api_url}/locations/{location_id}", timeout=self.timeout)
        if response.status_code == 404:
            response = self.session.delete(f"{self.api_url}/entities/{location_id}", timeout=self.timeout)
        self._raise_for_status(response, "Homebox location deletion failed")
        return response.status_code in (200, 202, 204)

    def suggest_tags_and_locations(self, ai_data: dict) -> dict:
        """Return lightweight suggestions from existing Homebox tags/locations."""
        if not self.authenticated:
            return {"tags": [], "locations": [], "reason": "not authenticated"}

        wanted = []
        for key in ("name", "category", "model", "suggested_location"):
            value = ai_data.get(key)
            if value:
                wanted.append(str(value))
        wanted.extend(str(tag) for tag in ai_data.get("tags") or [])
        tokens = self._tokens(" ".join(wanted))

        def score_name(row: dict) -> int:
            name = str(row.get("name") or "")
            hay = self._tokens(name + " " + str(row.get("description") or ""))
            return len(tokens & hay)

        tags = sorted(self.get_tags(), key=score_name, reverse=True)
        locations = sorted(self.get_locations(), key=score_name, reverse=True)
        return {
            "tags": [tag for tag in tags if score_name(tag) > 0][:8],
            "locations": [loc for loc in locations if score_name(loc) > 0][:8],
            "reason": "matched existing tag/location names",
        }

    @staticmethod
    def _clean_name(value) -> str:
        return str(value or "").strip()

    @classmethod
    def _unique_names(cls, values: Iterable[str]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for value in values:
            name = cls._clean_name(value)
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        return names

    @classmethod
    def _exact_id_map(cls, rows: Iterable[dict]) -> dict[str, str]:
        exact: dict[str, str] = {}
        for row in rows:
            name = cls._clean_name(row.get("name"))
            row_id = row.get("id")
            if name and row_id and name not in exact:
                exact[name] = row_id
        return exact

    def resolve_location_id_exact(self, name: Optional[str]) -> Optional[str]:
        """Resolve a Homebox location only when the name matches exactly."""
        if not self.authenticated:
            return None
        target = self._clean_name(name)
        if not target:
            return None
        return self._exact_id_map(self.get_locations()).get(target)

    def ensure_tags(self, tag_names: Iterable[str]) -> list[str]:
        if not self.authenticated:
            return []

        wanted = self._unique_names(tag_names)
        if not wanted:
            return []

        existing = self._exact_id_map(self.get_tags())
        tag_ids: list[str] = []
        for name in wanted:
            if name in existing:
                tag_ids.append(existing[name])
                continue

            tag = self.create_tag(name, description="Created by O.R.B.I.T.")
            tag_ids.append(tag["id"])
            existing[name] = tag["id"]
        return tag_ids

    def create_item(
        self,
        ai_data: dict,
        image_cv2=None,
        weight_g: Optional[float] = None,
        dimensions: Optional[dict] = None,
        location_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Optional[dict]:
        name = (ai_data.get("name") or "未命名物品").strip()[:255]
        asset_id = self._asset_id_for(ai_data, dry_run=dry_run)
        ai_data.setdefault("assetId", asset_id)
        description = self._build_description(ai_data, weight_g, dimensions)
        tag_names = []
        for tag in ai_data.get("tags") or []:
            tag_names.append(str(tag))

        tag_ids = [] if dry_run else self.ensure_tags(tag_names)
        resolved_location_id = location_id
        if not resolved_location_id and not dry_run and ai_data.get("suggested_location"):
            try:
                resolved_location_id = self.resolve_location_id_exact(ai_data.get("suggested_location"))
            except HomeboxError as exc:
                print(f"Homebox location exact match skipped: {exc}")
        payload = {
            "name": name,
            "assetId": asset_id,
            "description": description[:1000],
            "quantity": int(ai_data.get("quantity") or 1),
            "tagIds": tag_ids,
            "manufacturer": str(ai_data.get("manufacturer") or "").strip(),
            "modelNumber": str(ai_data.get("model") or "").strip(),
        }
        if resolved_location_id:
            payload["locationId"] = resolved_location_id

        print(f"Homebox item payload: {payload}")
        if dry_run:
            return {"id": "dry-run", **payload}
        if not self.authenticated:
            raise HomeboxError("Homebox token/login is missing; set HOMEBOX_TOKEN or HOMEBOX_USERNAME/PASSWORD")

        response = self.session.post(f"{self.api_url}/items", json=payload, timeout=self.timeout)
        entity_mode = False
        if response.status_code == 404:
            entity_payload = {
                "name": payload["name"],
                "assetId": asset_id,
                "description": payload.get("description", ""),
                "quantity": payload.get("quantity", 1),
                "tagIds": tag_ids,
            }
            if resolved_location_id:
                entity_payload["parentId"] = resolved_location_id
            response = self.session.post(f"{self.api_url}/entities", json=entity_payload, timeout=self.timeout)
            entity_mode = True
        self._raise_for_status(response, "Homebox item creation failed")
        item = response.json()
        if isinstance(item, dict) and asset_id:
            item.setdefault("assetId", asset_id)
        item_id = item.get("id")
        print(f"Homebox item created: {item_id}")

        if item_id:
            self.update_item_details(
                item_id,
                payload,
                ai_data,
                weight_g,
                dimensions,
                tag_ids,
                resolved_location_id,
                entity_mode=entity_mode,
            )
            if image_cv2 is not None:
                self.upload_image(item_id, image_cv2)
        return item

    def update_item_details(
        self,
        item_id: str,
        create_payload: dict,
        ai_data: dict,
        weight_g: Optional[float],
        dimensions: Optional[dict],
        tag_ids: list[str],
        location_id: Optional[str],
        entity_mode: bool = False,
    ) -> None:
        fields = self._build_fields(ai_data, weight_g, dimensions)
        notes = self._build_notes(ai_data)
        if not fields and not notes:
            return

        payload = {
            "id": item_id,
            "name": create_payload["name"],
            "assetId": create_payload.get("assetId") or self._asset_id_for(ai_data, item_id=item_id),
            "description": create_payload.get("description", ""),
            "quantity": create_payload.get("quantity", 1),
            "tagIds": tag_ids,
            "manufacturer": str(ai_data.get("manufacturer") or create_payload.get("manufacturer") or "").strip(),
            "modelNumber": str(ai_data.get("model") or create_payload.get("modelNumber") or "").strip(),
            "fields": fields,
            "notes": notes,
        }
        if location_id:
            payload["parentId" if entity_mode else "locationId"] = location_id

        endpoint = "entities" if entity_mode else "items"
        response = self.session.put(f"{self.api_url}/{endpoint}/{item_id}", json=payload, timeout=self.timeout)
        if response.status_code == 404 and not entity_mode:
            payload.pop("locationId", None)
            if location_id:
                payload["parentId"] = location_id
            response = self.session.put(f"{self.api_url}/entities/{item_id}", json=payload, timeout=self.timeout)
        self._raise_for_status(response, "Homebox item detail update failed")

    def _asset_id_for(self, ai_data: dict, item_id: str | None = None, dry_run: bool = False) -> str:
        explicit = str(ai_data.get("assetId") or ai_data.get("asset_id") or ai_data.get("code") or "").strip()
        if explicit:
            return HomeboxClient._normalize_asset_id(explicit)
        if self.authenticated and not dry_run:
            return self._next_intake_asset_id()
        return self._fallback_intake_asset_id(item_id=item_id, ai_data=ai_data)

    def _next_intake_asset_id(self, date_prefix: Optional[str] = None) -> str:
        prefix = date_prefix or datetime.now().strftime("%y%m%d")
        max_seq = 0
        for item in self._iter_inventory_items():
            digits = self._asset_digits(item.get("assetId"))
            if len(digits) >= 10 and digits.startswith(prefix):
                try:
                    max_seq = max(max_seq, int(digits[6:10]))
                except ValueError:
                    continue
        seq = max_seq + 1
        if seq > 9999:
            raise HomeboxError(f"asset ID daily sequence exhausted for {prefix}")
        return f"{prefix}{seq:04d}"

    def _iter_inventory_items(self):
        page_size = 100
        for page in range(1, 101):
            data = self.list_items(page=page, page_size=page_size)
            rows = self._items_from_list_response(data)
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                entity_type = row.get("entityType") if isinstance(row.get("entityType"), dict) else {}
                if entity_type.get("isLocation"):
                    continue
                yield row
            total = data.get("total") if isinstance(data, dict) else None
            if isinstance(total, int) and page * page_size >= total:
                break
            if len(rows) < page_size:
                break

    @staticmethod
    def _items_from_list_response(data) -> list[dict]:
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if not isinstance(data, dict):
            return []
        for key in ("items", "entities", "data", "results"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return []

    @classmethod
    def _fallback_intake_asset_id(cls, item_id: str | None = None, ai_data: Optional[dict] = None) -> str:
        prefix = datetime.now().strftime("%y%m%d")
        source = "|".join(
            str(part or "")
            for part in (
                item_id,
                (ai_data or {}).get("name"),
                (ai_data or {}).get("manufacturer"),
                (ai_data or {}).get("model"),
            )
        )
        value = int.from_bytes(hashlib.blake2s(source.encode("utf-8"), digest_size=4).digest(), "big")
        return f"{prefix}{value % 10000:04d}"

    @staticmethod
    def _normalize_asset_id(value: str) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        digits = HomeboxClient._asset_digits(text)
        if len(digits) >= 10:
            return digits[:10]
        if len(digits) >= 3:
            return digits[:32]
        seed = re.sub(r"[^A-Z0-9]", "", text) or text
        value_int = int.from_bytes(hashlib.blake2s(seed.encode("utf-8"), digest_size=4).digest(), "big")
        return f"{datetime.now().strftime('%y%m%d')}{value_int % 10000:04d}"

    @staticmethod
    def _asset_digits(value) -> str:
        text = str(value or "").strip().upper()
        if text.startswith("ORB-"):
            text = text[4:]
        elif text.startswith("ORB"):
            text = text[3:]
        return re.sub(r"\D", "", text)

    def upload_image(self, item_id: str, image_cv2) -> None:
        ok, img_encoded = cv2.imencode(".jpg", image_cv2, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            raise HomeboxError("OpenCV failed to encode item image")

        files = {"file": ("orbit-scan.jpg", img_encoded.tobytes(), "image/jpeg")}
        data = {"type": "photo", "primary": "true", "name": "orbit-scan.jpg"}
        response = self.session.post(
            f"{self.api_url}/items/{item_id}/attachments",
            files=files,
            data=data,
            timeout=max(self.timeout, 60),
        )
        if response.status_code == 404:
            response = self.session.post(
                f"{self.api_url}/entities/{item_id}/attachments",
                files=files,
                data=data,
                timeout=max(self.timeout, 60),
            )
        self._raise_for_status(response, "Homebox image upload failed")
        print("Homebox image uploaded")

    def _location_entity_type_id(self) -> Optional[str]:
        response = self.session.get(f"{self.api_url}/entity-types", timeout=self.timeout)
        self._raise_for_status(response, "Homebox entity type query failed")
        for row in response.json() or []:
            if row.get("isLocation"):
                return row.get("id")
        return None

    @classmethod
    def _flatten_location_tree(cls, rows, parent_id: Optional[str] = None) -> list[dict]:
        locations: list[dict] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if row.get("type") == "location" and row_id and row.get("name"):
                locations.append(
                    {
                        "id": row_id,
                        "name": row.get("name"),
                        "description": row.get("description") or "",
                        "parentId": parent_id,
                        "type": "location",
                    }
                )
            locations.extend(cls._flatten_location_tree(row.get("children") or [], row_id))
        return locations

    def _build_description(
        self, ai_data: dict, weight_g: Optional[float], dimensions: Optional[dict]
    ) -> str:
        lines = [str(ai_data.get("description") or "").strip()]
        if ai_data.get("manufacturer"):
            lines.append(f"制造商: {ai_data['manufacturer']}")
        if ai_data.get("category"):
            lines.append(f"分类建议: {ai_data['category']}")
        if weight_g is not None:
            lines.append(f"重量: {weight_g:.1f} g")
        if dimensions:
            width = dimensions.get("width_mm")
            height = dimensions.get("height_mm")
            if width and height:
                lines.append(f"视觉尺寸: {width:.1f} x {height:.1f} mm")
        lines.append(f"入库时间: {datetime.now().isoformat(timespec='seconds')}")
        return "\n".join(line for line in lines if line)

    def _build_notes(self, ai_data: dict) -> str:
        lines = []
        if ai_data.get("reasoning"):
            lines.append(f"AI推理: {ai_data['reasoning']}")
        if ai_data.get("suggested_location"):
            lines.append(f"建议位置: {ai_data['suggested_location']}")
        return "\n".join(lines)

    def _build_fields(
        self, ai_data: dict, weight_g: Optional[float], dimensions: Optional[dict]
    ) -> list[dict]:
        fields = []
        if weight_g is not None:
            fields.append({"name": "Weight", "type": "text", "textValue": f"{weight_g:.1f} g"})
        if dimensions:
            width = dimensions.get("width_mm")
            height = dimensions.get("height_mm")
            if width and height:
                fields.append({"name": "Measured Size", "type": "text", "textValue": f"{width:.1f} x {height:.1f} mm"})
        if ai_data.get("size"):
            fields.append({"name": "AI Size", "type": "text", "textValue": str(ai_data["size"])})
        return fields

    @staticmethod
    def _without_orbit_code_field(fields: Iterable[dict]) -> list[dict]:
        return [
            dict(row)
            for row in fields
            if isinstance(row, dict) and str(row.get("name") or "") != "O.R.B.I.T. Code"
        ]

    def _raise_for_status(self, response: requests.Response, prefix: str) -> None:
        if response.ok:
            return
        detail = response.text[:1000]
        raise HomeboxError(f"{prefix}: HTTP {response.status_code} {detail}")

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token.casefold()
            for token in re.split(r"[\s,，;；/\\|()（）\[\]【】:_\-+.]+", text)
            if len(token.strip()) >= 2
        }

    # Backward compatibility with the original main.py naming.
    def create_asset(self, ai_data, image_cv2=None):
        return bool(self.create_item(ai_data, image_cv2=image_cv2))

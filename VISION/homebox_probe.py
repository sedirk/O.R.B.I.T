import argparse
import json
import os
import time

from homebox import HomeboxClient, HomeboxError


def build_parser():
    parser = argparse.ArgumentParser(description="Probe Homebox read/write/delete capabilities.")
    parser.add_argument("--homebox-url", default=os.getenv("HOMEBOX_URL", "http://192.168.31.3:3100"))
    parser.add_argument("--homebox-token", default=os.getenv("HOMEBOX_TOKEN"))
    parser.add_argument("--homebox-username", default=os.getenv("HOMEBOX_USERNAME"))
    parser.add_argument("--homebox-password", default=os.getenv("HOMEBOX_PASSWORD"))
    parser.add_argument("--write-test", action="store_true", help="Create and delete O.R.B.I.T. test records.")
    parser.add_argument("--keep-test-records", action="store_true")
    return parser


def print_json(label, data):
    print(f"{label}:")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    args = build_parser().parse_args()
    client = HomeboxClient(
        args.homebox_url,
        token=args.homebox_token,
        username=args.homebox_username,
        password=args.homebox_password,
    )
    health = client.health()
    print_json("health", health)
    print(f"authenticated: {client.authenticated}")

    if not client.authenticated:
        print("read/write/delete: unavailable (401 without HOMEBOX_TOKEN or HOMEBOX_USERNAME/PASSWORD)")
        return 2

    tags = client.get_tags()
    locations = client.get_locations()
    items = client.list_items(page_size=5)
    print(f"tags: {len(tags)}")
    print(f"locations: {len(locations)}")
    print(f"items page: {len(items.get('items') or [])} / total={items.get('total')}")

    suggestion = client.suggest_tags_and_locations(
        {
            "name": "Canon RF 28-70mm F2",
            "category": "相机镜头",
            "model": "RF28-70F2",
            "tags": ["Canon", "镜头", "摄影"],
            "suggested_location": "相机镜头",
        }
    )
    print_json("suggestion", suggestion)

    if not args.write_test:
        print("write/delete test skipped; pass --write-test to exercise mutations.")
        return 0

    suffix = time.strftime("%Y%m%d-%H%M%S")
    existing_tag_ids = {tag.get("id") for tag in tags}
    tag = None
    location = None
    item = None
    try:
        tag = client.create_tag(f"O.R.B.I.T. Probe {suffix}", description="temporary API probe")
        location = client.create_location(f"O.R.B.I.T. Probe Bin {suffix}", description="temporary API probe")
        item = client.create_item(
            {
                "name": f"O.R.B.I.T. API Probe {suffix}",
                "category": f"O.R.B.I.T. Probe {suffix}",
                "model": "probe",
                "quantity": 1,
                "tags": [],
                "description": "temporary item created by O.R.B.I.T. probe",
                "reasoning": "safe write/delete validation",
            },
            weight_g=1.0,
            dimensions={"width_mm": 40.0, "height_mm": 20.0},
            location_id=location.get("id"),
            dry_run=False,
        )
        fetched = client.get_item(item["id"])
        print_json("created_item", fetched)
    finally:
        if not args.keep_test_records:
            if item and item.get("id"):
                try:
                    print(f"delete item: {client.delete_item(item['id'])}")
                except HomeboxError as exc:
                    print(f"delete item failed: {exc}")
            if tag and tag.get("id"):
                try:
                    print(f"delete tag: {client.delete_tag(tag['id'])}")
                except HomeboxError as exc:
                    print(f"delete tag failed: {exc}")
            for created_tag in client.get_tags():
                if (
                    created_tag.get("id") not in existing_tag_ids
                    and str(created_tag.get("name") or "").startswith("O.R.B.I.T. Probe")
                ):
                    try:
                        print(f"delete probe category tag: {client.delete_tag(created_tag['id'])}")
                    except HomeboxError as exc:
                        print(f"delete probe category tag failed: {exc}")
            if location and location.get("id"):
                try:
                    print(f"delete location: {client.delete_location(location['id'])}")
                except HomeboxError as exc:
                    print(f"delete location failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

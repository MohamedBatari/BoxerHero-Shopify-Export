import os
import time
import math
import requests
import pandas as pd
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
import pytz

load_dotenv()

CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

API_VERSION = "2026-04"
START_DATE = "2025-01-01 00:00:00"
END_DATE = "2026-05-17 23:59:59"
REPORTING_TZ = pytz.timezone("Europe/Paris")

STORES = [
    {
        "name": "boxerhero",
        "domain": "boxerhero.myshopify.com",
        "output": "output/shopify_boxerhero_sku_20250101_20260517.xlsx",
    },
    {
        "name": "the-gentside-shop",
        "domain": "the-gentside-shop.myshopify.com",
        "output": "output/shopify_the-gentside-shop_sku_20250101_20260517.xlsx",
    },
]


def require_env():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise Exception("Missing SHOPIFY_CLIENT_ID or SHOPIFY_CLIENT_SECRET in .env")


def get_access_token(shop_domain):
    print(f"Getting token for {shop_domain}...")
    url = f"https://{shop_domain}/admin/oauth/access_token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }

    response = requests.post(url, json=payload, timeout=180)
    if response.status_code >= 400:
        print(response.text)
        response.raise_for_status()

    data = response.json()
    token = data.get("access_token")
    if not token:
        raise Exception(f"No access_token returned for {shop_domain}: {data}")

    return token


def get_next_page_url(link_header):
    if not link_header:
        return None

    parts = link_header.split(",")
    for part in parts:
        if 'rel="next"' in part:
            start = part.find("<") + 1
            end = part.find(">")
            return part[start:end]

    return None


def request_with_retries(url, headers, params=None, max_retries=6):
    for attempt in range(max_retries):
        response = requests.get(url, headers=headers, params=params, timeout=90)

        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", "2"))
            print(f"Rate limited. Waiting {wait}s...")
            time.sleep(wait)
            continue

        if response.status_code in [500, 502, 503, 504]:
            wait = 2 ** attempt
            print(f"Server error {response.status_code}. Retry in {wait}s...")
            time.sleep(wait)
            continue

        if response.status_code >= 400:
            print(response.text)
            response.raise_for_status()

        return response

    raise Exception(f"Failed after retries: {url}")


def fetch_orders(shop_domain, token):
    print(f"Fetching orders for {shop_domain}...")

    start_local = REPORTING_TZ.localize(datetime.strptime(START_DATE, "%Y-%m-%d %H:%M:%S"))
    end_local = REPORTING_TZ.localize(datetime.strptime(END_DATE, "%Y-%m-%d %H:%M:%S"))

    created_at_min = start_local.isoformat()
    created_at_max = end_local.isoformat()

    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    url = f"https://{shop_domain}/admin/api/{API_VERSION}/orders.json"
    params = {
        "status": "any",
        "limit": 250,
        "created_at_min": created_at_min,
        "created_at_max": created_at_max,
        "fields": ",".join([
            "id",
            "name",
            "created_at",
            "cancelled_at",
            "financial_status",
            "currency",
            "total_price",
            "current_total_price",
            "subtotal_price",
            "current_subtotal_price",
            "total_discounts",
            "line_items",
            "refunds",
        ]),
    }

    all_orders = []
    page = 1

    while True:
        response = request_with_retries(url, headers, params=params)
        orders = response.json().get("orders", [])
        all_orders.extend(orders)

        print(f"Page {page}: fetched {len(orders)} orders | total so far: {len(all_orders)}")

        next_url = get_next_page_url(response.headers.get("Link"))
        if not next_url:
            break

        url = next_url
        params = None
        page += 1
        time.sleep(0.5)

    return all_orders


def parse_money(value):
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


def get_refund_amount(order):
    refunds = order.get("refunds") or []
    total = 0.0

    for refund in refunds:
        transactions = refund.get("transactions") or []
        for tx in transactions:
            if tx.get("kind") == "refund" and tx.get("status") == "success":
                total += parse_money(tx.get("amount"))

    return total


def get_refunded_quantity_by_line(order):
    result = {}
    refunds = order.get("refunds") or []

    for refund in refunds:
        refund_line_items = refund.get("refund_line_items") or []
        for rli in refund_line_items:
            line_item_id = rli.get("line_item_id")
            qty = int(rli.get("quantity") or 0)
            result[line_item_id] = result.get(line_item_id, 0) + qty

    return result


def week_start(dt):
    return dt.to_period("W-MON").start_time.date().isoformat()


def build_dataframes(orders):
    order_rows = []
    line_rows = []

    for order in orders:
        created_at_raw = order.get("created_at")
        created_dt = pd.to_datetime(created_at_raw, utc=True).tz_convert("Europe/Paris")
        week = week_start(created_dt)

        refund_amount = get_refund_amount(order)
        refunded_by_line = get_refunded_quantity_by_line(order)

        line_items = order.get("line_items") or []

        gross_items = sum(int(li.get("quantity") or 0) for li in line_items)
        refunded_items = sum(refunded_by_line.values())
        net_items = gross_items - refunded_items

        gross_total = parse_money(order.get("total_price"))
        current_total = parse_money(order.get("current_total_price"))
        currency = order.get("currency")

        order_rows.append({
            "week": week,
            "created_at_paris": created_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "order_name": order.get("name"),
            "order_id": order.get("id"),
            "financial_status": order.get("financial_status"),
            "is_cancelled": bool(order.get("cancelled_at")),
            "cancelled_at": order.get("cancelled_at"),
            "currency": currency,
            "gross_total": gross_total,
            "current_total": current_total,
            "refund_amount": refund_amount,
            "gross_items": gross_items,
            "net_items": net_items,
        })

        for li in line_items:
            line_id = li.get("id")
            qty = int(li.get("quantity") or 0)
            refunded_qty = refunded_by_line.get(line_id, 0)
            net_qty = qty - refunded_qty

            price = parse_money(li.get("price"))
            total_discount = parse_money(li.get("total_discount"))

            gross_line_sales = qty * price
            discounted_line_sales = gross_line_sales - total_discount

            if qty > 0:
                estimated_net_line_sales = discounted_line_sales * (net_qty / qty)
            else:
                estimated_net_line_sales = 0.0

            line_rows.append({
                "week": week,
                "created_at_paris": created_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "order_name": order.get("name"),
                "order_id": order.get("id"),
                "financial_status": order.get("financial_status"),
                "is_cancelled": bool(order.get("cancelled_at")),
                "currency": currency,
                "line_item_id": line_id,
                "sku": li.get("sku") or "",
                "product_title": li.get("title") or "",
                "variant_title": li.get("variant_title") or "",
                "gross_quantity": qty,
                "refunded_quantity": refunded_qty,
                "net_quantity": net_qty,
                "unit_price": price,
                "gross_line_sales": gross_line_sales,
                "line_discount": total_discount,
                "discounted_line_sales": discounted_line_sales,
                "estimated_net_line_sales": estimated_net_line_sales,
            })

    orders_df = pd.DataFrame(order_rows)
    lines_df = pd.DataFrame(line_rows)

    if orders_df.empty:
        orders_df = pd.DataFrame(columns=[
            "week", "created_at_paris", "order_name", "order_id", "financial_status",
            "is_cancelled", "cancelled_at", "currency", "gross_total", "current_total",
            "refund_amount", "gross_items", "net_items"
        ])

    if lines_df.empty:
        lines_df = pd.DataFrame(columns=[
            "week", "created_at_paris", "order_name", "order_id", "financial_status",
            "is_cancelled", "currency", "line_item_id", "sku", "product_title",
            "variant_title", "gross_quantity", "refunded_quantity", "net_quantity",
            "unit_price", "gross_line_sales", "line_discount", "discounted_line_sales",
            "estimated_net_line_sales"
        ])

    weekly_summary = (
        orders_df
        .groupby(["week", "currency"], dropna=False)
        .agg(
            gross_orders=("order_id", "count"),
            net_orders=("is_cancelled", lambda x: int((~x).sum())),
            gross_sales=("gross_total", "sum"),
            current_net_sales=("current_total", "sum"),
            refunds=("refund_amount", "sum"),
            gross_items=("gross_items", "sum"),
            net_items=("net_items", "sum"),
        )
        .reset_index()
        .sort_values(["week", "currency"])
    )

    if not lines_df.empty:
        sku_weekly_detail = (
            lines_df
            .groupby(["week", "currency", "sku", "product_title", "variant_title"], dropna=False)
            .agg(
                order_count_containing_sku=("order_id", "nunique"),
                gross_quantity=("gross_quantity", "sum"),
                refunded_quantity=("refunded_quantity", "sum"),
                net_quantity=("net_quantity", "sum"),
                gross_line_sales=("gross_line_sales", "sum"),
                discounted_line_sales=("discounted_line_sales", "sum"),
                estimated_net_line_sales=("estimated_net_line_sales", "sum"),
            )
            .reset_index()
            .sort_values(["week", "sku"])
        )
    else:
        sku_weekly_detail = pd.DataFrame()

    return weekly_summary, sku_weekly_detail, orders_df, lines_df


def write_excel(output_path, weekly_summary, sku_weekly_detail, orders_df, lines_df):
    print(f"Writing Excel: {output_path}")

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        weekly_summary.to_excel(writer, sheet_name="Weekly Summary", index=False)
        sku_weekly_detail.to_excel(writer, sheet_name="SKU Weekly Detail", index=False)
        orders_df.to_excel(writer, sheet_name="Order Audit", index=False)
        lines_df.to_excel(writer, sheet_name="Line Audit", index=False)

        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            ws.freeze_panes = "A2"
            for column_cells in ws.columns:
                max_length = 0
                col_letter = column_cells[0].column_letter
                for cell in column_cells:
                    value = str(cell.value) if cell.value is not None else ""
                    max_length = max(max_length, len(value))
                ws.column_dimensions[col_letter].width = min(max_length + 2, 40)


def main():
    require_env()
    os.makedirs("output", exist_ok=True)

    for store in STORES:
        print("=" * 80)
        print(f"Processing store: {store['name']} | {store['domain']}")

        token = get_access_token(store["domain"])
        orders = fetch_orders(store["domain"], token)

        weekly_summary, sku_weekly_detail, orders_df, lines_df = build_dataframes(orders)
        write_excel(store["output"], weekly_summary, sku_weekly_detail, orders_df, lines_df)

        print(f"Done: {store['output']}")

    print("=" * 80)
    print("All exports completed.")


if __name__ == "__main__":
    main()
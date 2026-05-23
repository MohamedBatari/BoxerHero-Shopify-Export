# BoxerHero Shopify Data Extraction

## Overview

This project extracts historical Shopify order data for two stores:

- BoxerHero (`boxerhero.myshopify.com`)
- BoxerHero US (`the-gentside-shop.myshopify.com`)

The export covers orders from `2025-01-01 00:00:00` to `2026-05-17 23:59:59` using the `Europe/Paris` reporting timezone.

The output is one Excel workbook per store.

## Output Files

Generated files are stored in the `output` folder:

- `shopify_boxerhero_sku_20250101_20260517.xlsx`
- `shopify_the-gentside-shop_sku_20250101_20260517.xlsx`

Each workbook includes:

1. `Weekly Summary`
2. `SKU Weekly Detail`
3. `Order Audit`
4. `Line Audit`

## Setup

Install dependencies:

```bash
python -m pip install -r requirements.txt
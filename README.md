# ArbitrageOS

The back office for an Amazon FBA arbitrage business: find and score deals,
record what you buy, print FNSKU labels, build shipments, set repricer
floors and ceilings, and track profit against the $10,000-a-month goal.

It runs on this Mac only (http://127.0.0.1:8787), uses nothing but the Python
that ships with macOS, and keeps everything in `data/` inside this folder.

## Start it

**Mac app (recommended):** double-click **Install Mac App.command** once. It
builds ArbitrageOS.app into your Applications folder, pins it to the Dock and
opens it. The app starts the server, shows the dashboard in its own window,
and puts a tag icon in the menu bar (open any screen, Sync now, Quit).
Closing the window keeps it in the menu bar; Quit stops the server. Run the
installer again after updating the code.

**Without the app:** double-click **ArbitrageOS.command** in Finder. The first time, macOS may ask
you to confirm: right-click it, choose Open, then Open again. The dashboard
opens in your browser. Close the Terminal window to stop it.

From Terminal instead:

```
cd ~/Developer/ArbitrageOS
python3 -m arbos serve --open
```

If macOS says python3 is missing, run `xcode-select --install` once.

## Demo mode and live mode

It starts in **demo mode**: a fictional catalog (made-up brands, made-up
numbers) and three months of simulated history, so every screen works today.
Nothing in demo mode is real market data.

To go live (Settings):

1. **Keepa API key** (EUR 49/month, 20 tokens a minute, about 28,000 product
   lookups a day). Turns on real prices, sales rank, offer counts and
   package sizes.
2. **Amazon Selling Partner API** for your own seller account. In Seller
   Central go to Apps and Services > Develop Apps, add a private app with the
   Pricing, Inventory and Order Tracking, Product Listing, and Finance and
   Accounting roles, then Authorize it. Paste the LWA client ID, client
   secret, refresh token and your seller ID (Settings > Account Info >
   Merchant Token). This turns on gating checks, Amazon's own fee estimates,
   exact fees per order, refunds, FBA inventory and FNSKUs, and pushing prices.
3. Switch Mode to Live and save. Then Settings > Wipe all data to clear the
   demo history before your first real purchase.

Amazon cancelled its planned Selling Partner API fees in May 2026, and
sellers using it for their own account were never meant to pay them.

## Find deals (one click)

**Find deals > Find best deals** (or the menu bar's Find Best Deals Now)
pulls discounted products from every ready source, matches each to its
Amazon listing, runs the fee stack and buy filter, and ranks the BUY and
MAYBE results by margin, ROI, profit per unit or profit per month.

| Source | Needs | What it finds |
| --- | --- | --- |
| Demo retailer deals | demo mode | fictional deals to try the finder |
| Best Buy sale and clearance | free key from developer.bestbuy.com + Keepa | on-sale items 30%+ off, with UPCs |
| Amazon price drops (flips) | Keepa | Amazon's own price drops; buy now, resell at the 90-day average |
| Your lead feeds | CSV links from a lead-list service + Keepa | whatever the service sends |

Each product checked costs about 3 Keepa tokens, so the 20-token plan
checks about 7 products a minute; a 60-product scan takes roughly 9
minutes and runs in the background. With "scan every morning" on in
Settings, the daily job runs a scan too, and new BUY deals show up in the
Overview alerts. Repeat scans refresh existing leads instead of duplicating
them. Walmart and Target have no open price API, so for those use a lead
feed, Tactical Arbitrage exports, or SellerAmp in the store.

## The daily loop

| Step | Screen | What happens |
| --- | --- | --- |
| Find | Leads > Import CSV | Any CSV with a cost column and a UPC or ASIN column (Tactical Arbitrage exports, lead-list services, your own sheet). Each row is matched to Amazon and scored. |
| Check | Check a deal | One product: ASIN, UPC or Amazon link plus your cost. Full fee stack, buy filter, most you should pay, how many to buy. |
| Buy | Leads > Buy, or Inventory > Record a purchase | Attach the order page or receipt. Landed cost includes tax, cashback, prep and inbound shipping. |
| Prep | Inventory > Print labels | FNSKU labels on Avery 5160/5167 sheets or 2.25 x 1.25 / 2 x 1 thermal rolls. |
| Ship | Inventory > Create shipment, then Shipments | Create the matching shipment in Seller Central (Send to Amazon) with Amazon-optimized splits, enter its ID, mark it shipped. |
| Price | Repricing | Floor keeps 10% ROI and $1 profit; ceiling stops chasing spikes. Download for Seller Central or BQool/Aura, or push via the API. |
| Track | Overview, Sales and profit | Monthly P&L, ROI, inventory turns, months to goal, alerts. |

## Scheduled daily run

```
./scripts/install_daily_job.sh          # every day at 6:10 am
./scripts/install_daily_job.sh --remove
```

Runs `python3 -m arbos daily`: Amazon sync, rescoring of open leads, alerts.
Log in `data/daily.log`. The dashboard does not need to be open.

## Command line

```
python3 -m arbos deals 60                    # one-click deal scan from the terminal
python3 -m arbos check 000000100031 4.49     # score one deal
python3 -m arbos import leads.csv            # import and score leads
python3 -m arbos sync                        # pull Amazon sales and inventory
python3 -m arbos prices amazon               # write a price file to data/exports
python3 -m arbos alerts
```

## What the numbers assume

- 2026 US FBA fulfillment rate card (from Jan 15), holiday peak rates
  Oct 15 - Jan 14, and the 3.5% fuel and logistics surcharge (from Apr 17).
  Referral fees by category with tiers and the $0.30 minimum. Storage at
  $0.78 per cubic foot (Jan-Sep) and $2.40 (Oct-Dec). Inbound placement
  fees by split option. When the Selling Partner API is connected, Amazon's
  own fee estimate replaces the rate card for referral and fulfillment.
- Sell price is the current Buy Box, capped near the 90-day average when the
  current price is a spike.
- Monthly sales come from Amazon's "bought in past month" figure, else Keepa
  sales-rank drops, else a conservative rank curve.
- Your share of sales = listing sales / (FBA sellers + 1).
- Buy filter defaults come from the plan: 30% ROI (25% for fast sellers),
  $3 profit, 30+ sales a month, 9 or fewer FBA sellers, Amazon not on the
  listing, price near its 90-day average, ungated, no IP-complaint brand,
  no hazmat, standard size, no more than 10% of cash in one SKU. Change any
  of them in Settings.

Fee tables change. Check them against Seller Central's Fee Preview report
each January and October; they live in `arbos/fees.py`.

## Files

```
ArbitrageOS.command      launcher without the app
Install Mac App.command  builds the Dock and menu bar app
macapp/                  app source (Swift) and icon
arbos/                   the app (Python standard library only)
  fees.py                2026 fee engine
  scoring.py             profit, ROI and the buy filter
  providers/             Keepa, Amazon SP-API and demo data sources
  leads.py ledger.py pnl.py repricing.py labels.py alerts.py sync.py
  web/                   the dashboard
data/                    your database, receipts and exports (back this up)
samples/leads_example.csv
scripts/install_daily_job.sh
tests/                   python3 -m unittest discover -s tests
```

Security: the server only answers on 127.0.0.1, rejects other Host headers
and cross-site posts, and stores API keys in `data/config.json` readable
only by your user account.

# Stock Clerk

A demo hardware & home web store with:

1. **Semantic search** — shoppers describe a product by shape, color, or use ("small L-shaped metal tool for furniture bolts") and find it without knowing its name. Uses Gemini embeddings.
2. **Recommendations** — item-based collaborative filtering over purchase history ("customers who bought this also bought", "recommended for you").
3. **Support chatbot** — answers refund, return, shipping, and product questions, grounded in the store policy document and the catalog. Uses a Gemini chat model.
4. **Checkout** — Stripe Checkout (test mode). Every paid order is added to the purchase history, so recommendations learn from what shoppers actually buy.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then put your Gemini API key in .env
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000.

The first start embeds all 90 products in one batch, which uses most of the free tier's 100 embedding requests per minute, so searches may fall back to keyword matching for the first minute. The embeddings are then cached in `data/cache/` and later starts make no embedding calls.

### Stripe checkout

Add your Stripe **test-mode secret key** (`sk_test_...`, from https://dashboard.stripe.com/test/apikeys) to `.env` as `STRIPE_SECRET_KEY` and restart. The cart's Checkout button then opens Stripe's hosted payment page; pay with the test card `4242 4242 4242 4242`, any future expiry, and any CVC. The publishable key (`pk_test_...`) is not needed. Live keys are ignored on purpose, because this is a demo store.

Without a Stripe key, checkout is simulated: no payment page, but the order is still recorded and feeds the recommender.

Optional webhook, so orders are recorded even if the shopper closes the tab before returning:

```bash
stripe listen --forward-to localhost:8000/api/stripe/webhook
```

Put the signing secret it prints in `.env` as `STRIPE_WEBHOOK_SECRET`.

Without a Gemini API key the app still runs: search falls back to TF-IDF keyword ranking and the chatbot returns the best matching policy section.

## Deploy (public URL)

The repo includes a Render blueprint (`render.yaml`), so it deploys to a free `https://stock-clerk-xxxx.onrender.com` URL:

1. Push this folder to a GitHub repository.
2. In the Render dashboard choose **New → Blueprint**, connect the repository, and apply it.
3. When asked, paste `GEMINI_API_KEY` and `STRIPE_SECRET_KEY` (test key). They are stored in Render, not in the repo.

Notes for a public demo:

- The free plan sleeps after 15 minutes without traffic; the first visit after that takes about a minute to wake up.
- The disk is reset on every deploy and restart, so `data/orders.csv` (checkout orders) starts empty each time. The seed purchase history in `purchases.csv` always remains.
- The product embeddings in `data/cache/` are committed, so a new deploy doesn't need to re-embed the catalog.
- `app/ratelimit.py` limits chat, search, and checkout per visitor IP, plus a shared daily cap on AI calls, so visitors can't use up your Gemini quota.

## How it works

| Feature | Code | Approach |
| --- | --- | --- |
| Semantic search | `app/search.py` | Each product's name + description is embedded once with `gemini-embedding-001` (`RETRIEVAL_DOCUMENT`). A query is embedded with `RETRIEVAL_QUERY` and products are ranked by cosine similarity. Results far below the best match are dropped. |
| Recommendations | `app/recommender.py` | Builds a shopper × product purchase matrix, then item–item cosine similarity: `sim(i, j) = bought both / sqrt(bought i × bought j)`. Recommendations for a shopper or cart are the products with the highest summed similarity to what they already have. Each one reports which owned product it was "bought with". |
| Support chatbot | `app/chatbot.py` | Retrieval-augmented: the policy document plus the catalog products most relevant to the conversation (found with the semantic search) go into the system prompt. The model returns JSON with its reply and the product ids it mentions, which the UI shows as clickable chips. |
| Checkout | `app/checkout.py` | The server prices the cart from its own catalog and creates a Stripe Checkout Session. When the shopper returns, the server asks Stripe whether the session is paid, then appends the order to `data/orders.csv` (once per session id) and refits the recommender. Shipping is $5.99, free over $35, matching the policy file. |
| Gemini client | `app/gemini.py` | Small REST client. Chat tries each model in `GEMINI_CHAT_MODELS` in order and skips one that is overloaded. |
| API + static site | `app/main.py`, `static/` | FastAPI serves the JSON API and a plain HTML/CSS/JS storefront. |

## Data

Everything in `data/` is sample data and can be replaced:

- `products.json` — the catalog (`id`, `name`, `category`, `price`, `emoji`, `description`). Changing it re-embeds on the next start.
- `purchases.csv` — purchase history (`user_id`, `product_id`, `quantity`, `purchased_at`). Regenerate the synthetic history with `python scripts/generate_purchases.py`, or drop in real order data with the same columns.
- `orders.csv` — created by checkout: one row per item in each paid order. Loaded alongside `purchases.csv` at startup. Each browser gets a guest id, so "Recommended for you" uses that shopper's own past orders.
- `policies.md` — the store policies the chatbot answers from. Edit freely; `## ` headings define sections.

## Configuration (`.env`)

| Variable | Default |
| --- | --- |
| `GEMINI_API_KEY` | *(required for AI features)* |
| `GEMINI_EMBED_MODEL` | `gemini-embedding-001` |
| `GEMINI_CHAT_MODELS` | `gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.8-flash` |
| `GEMINI_THINKING_LEVEL` | `medium` |
| `STRIPE_SECRET_KEY` | *(optional; test-mode key, otherwise checkout is simulated)* |
| `STRIPE_WEBHOOK_SECRET` | *(optional)* |

## API

- `GET /api/search?q=...` — semantic search
- `GET /api/products`, `GET /api/products/{id}` — catalog; product details include `also_bought`
- `POST /api/recommendations` — body `{"shopper_id": "u012", "cart": ["plunger"]}`
- `POST /api/checkout` — body `{"cart": {"plunger": 2}, "shopper_id": "g-abc123"}`; returns the URL to send the shopper to
- `GET /api/checkout/confirm?session_id=...` — verifies payment and records the order
- `POST /api/stripe/webhook` — optional Stripe webhook
- `POST /api/chat` — body `{"messages": [{"role": "user", "content": "..."}]}`
- `GET /api/status`

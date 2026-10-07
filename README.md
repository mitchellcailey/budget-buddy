# Budget Buddy

A CLI that reads a `transactions.csv` with pandas and asks Claude to categorize your spending, then returns a budget with saving tips.

## How it works

1. **pandas** loads and cleans the CSV (`date`, `description`, `amount`; positive = spending, negative = income/refund).
2. **Claude** (`claude-sonnet-5-5`, adaptive thinking) runs an agentic tool-use loop. It calls `top_merchants`, `monthly_totals` and `search_transactions` to inspect the data.
3. The final answer is **structured output**: a JSON schema generated from the `BudgetReport` pydantic model and validated before printing.

## Usage

```bash
pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY, then export it
python budget-buddy.py transactions.csv
python budget-buddy.py transactions.csv --json
```

A sample `transactions.csv` is included. See [QUICKSTART.md](QUICKSTART.md) for a 2-minute walkthrough.

## Docker

```bash
docker build -t budget-buddy .
docker run --rm -e ANTHROPIC_API_KEY budget-buddy
```

## Tests

```bash
pytest
```

Tests cover CSV loading and the data tools, and mock the API, so no key or network is needed.

## License

MIT, see [LICENSE](LICENSE).

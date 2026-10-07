# Quickstart

1. Python 3.10+ is required. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set your key (never commit it):
   ```bash
   cp .env.example .env
   export ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Run it on the bundled sample:
   ```bash
   python budget-buddy.py transactions.csv
   ```
4. Use your own data: a CSV with columns `date,description,amount` (spending positive, income negative).
5. Run the tests: `pytest`

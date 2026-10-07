FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY budget-buddy.py transactions.csv ./
ENTRYPOINT ["python", "budget-buddy.py"]
CMD ["transactions.csv"]

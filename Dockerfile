FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mathbot ./mathbot
ENV PYTHONUNBUFFERED=1 DB_PATH=/data/mathbot.db
CMD ["python", "-m", "mathbot"]

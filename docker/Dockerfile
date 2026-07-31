FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8501

# Streamlit dashboard by default; override CMD to run the Telegram bot
# or the daily scan script in a separate container instead.
CMD ["streamlit", "run", "ui/app.py", "--server.address=0.0.0.0"]

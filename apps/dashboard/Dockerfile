FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY apps/dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY apps/dashboard/app.py .

EXPOSE 8080

CMD ["streamlit", "run", "app.py", \
     "--server.port=8080", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false"]

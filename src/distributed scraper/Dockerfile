FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://tailscale.com/install.sh | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.scraper.txt .
RUN pip install --no-cache-dir -r requirements.scraper.txt

COPY scraper_node.py config.py ./
COPY start.sh .
RUN chmod +x start.sh

CMD ["./start.sh"]

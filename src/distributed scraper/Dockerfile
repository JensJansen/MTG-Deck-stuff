FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://tailscale.com/install.sh | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY ["src/distributed scraper/requirements.scraper.txt", "."]
RUN pip install --no-cache-dir -r requirements.scraper.txt

COPY ["src/distributed scraper/scraper_node.py", "src/distributed scraper/config.py", "./"]
COPY ["src/distributed scraper/start.sh", "."]
RUN chmod +x start.sh

COPY src/constants/ ./constants/

CMD ["./start.sh"]

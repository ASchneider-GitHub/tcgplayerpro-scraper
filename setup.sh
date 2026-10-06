#!/bin/bash
docker network create web 2>/dev/null || true
docker build -t tcgplayerpro-scraper .
docker rm -f tcgplayerpro-scraper 2>/dev/null
docker run -d --name tcgplayerpro-scraper --restart unless-stopped --network web -v tcgplayerpro-scraper-data:/app/data tcgplayerpro-scraper
sleep 1
docker logs -f tcgplayerpro-scraper

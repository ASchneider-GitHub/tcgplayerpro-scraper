#!/bin/bash
docker build -t tcgplayerpro-scraper .
docker run -d --name tcgplayerpro-scraper -p 5000:5000 tcgplayerpro-scraper
sleep 1
docker logs -f tcgplayerpro-scraper

# tcgplayerpro-scraper
### Using questionable API calls to retrieve local store inventory for Magic: the Gathering singles.

#### 1. Clone & Prep
```
git clone https://github.com/ASchneider-GitHub/tcgplayerpro-scraper.git
cd tcgplayerpro-scraper
chmod +x *.sh
```

#### 2. Copy the template and fill in your Cloudflare keys:
```
cp cloudflare.env.example cloudflare.env
vim cloudflare.env
```

#### 3. Build & Launch
```
./setup.sh
```

#### 4. Add this to `crontab -e` to sync bad actors every 10 minutes:
```
*/10 * * * * /bin/bash $HOME/tcgplayerpro-scraper/update-ban-list.sh >> $HOME/tcgplayerpro-scraper/cron.log 2>&1
```


---

## 📋 Extra: Mini `README.md` (अगर छोटा चाहिए)

अगर पूरा README बहुत लंबा लगे, तो यह छोटा version use करें:

```markdown
# Jharkhand University Notification Monitor

Automated monitor for 24 Jharkhand universities. Scans official sites every 4-6 hours, extracts notices/results/admissions, and delivers to Telegram with Hindi + English support.

## Quick Start

```bash
git clone <repo>
cd jharkhand-university-monitor
pip install -r requirements.txt
python -m playwright install --with-deps chromium
sudo apt-get install -y tesseract-ocr tesseract-ocr-hin

export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export GEMINI_API_KEYS="key1,key2"

python monitor.py --test   # Run 52 self-tests
python monitor.py          # First run (baseline, no messages)

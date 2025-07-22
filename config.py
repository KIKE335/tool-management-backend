import os
from dotenv import load_dotenv

load_dotenv() # .env ファイルから環境変数をロード

# Google Sheets API 関連の設定
# ダウンロードしたサービスアカウントキーのJSONファイルへのパス
# 本番環境では環境変数で管理することを推奨
SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'service_account.json')
# アクセスするGoogleスプレッドシートのURLまたはID
# URL例: 'https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/edit'
# ID例: 'YOUR_SPREADSHEET_ID'
SPREADSHEET_ID = os.getenv('GOOGLE_SPREADSHEET_ID', '19iCIhu7x_LA0HBBaie2kh9yoZUsFVM65wBr3_pK-wiE')

# シート名
MASTER_SHEET_NAME = "MST工具治具"
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import gspread
import qrcode
from io import BytesIO
import base64
import datetime
from typing import Optional, List

# config.py から設定をインポート
from config import SERVICE_ACCOUNT_FILE, SPREADSHEET_ID, MASTER_SHEET_NAME

app = FastAPI()

# CORS設定: フロントエンドが別のオリジンで動作する場合に必要
origins = [
    "http://localhost:3000",  # React開発サーバーのURL
    # "https://your-frontend-domain.com", # デプロイ後のフロントエンドURL
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Google Sheets認証
try:
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID) # open_by_id を open_by_key に修正済みの前提
    master_sheet = spreadsheet.worksheet(MASTER_SHEET_NAME)
except Exception as e:
    print(f"Google Sheetsとの接続に失敗しました: {e}")
    print("サービスアカウントのJSONファイルパス、スプレッドシートID、シート名、共有設定を確認してください。")
    exit(1) # 起動時に接続エラーがあれば終了

# Pydantic モデル: フロントエンドからのリクエストデータとレスポンスデータの型定義
class ToolBase(BaseModel):
    # Pythonコードで扱いやすい変数名を使い、Field(alias="スプレッドシートの列名") でマッピング
    name: str = Field(..., alias="名称") # 名称
    modelNumber: Optional[str] = Field(None, alias="型番品番") # 型番品番 (スプレッドシートの正確な列名に合わせる)
    type: Optional[str] = Field(None, alias="種類") # 種類
    storageLocation: Optional[str] = Field(None, alias="保管場所") # 保管場所
    status: Optional[str] = Field("在庫", alias="状態") # 状態
    purchaseDate: Optional[str] = Field(None, alias="購入日") # 購入日
    purchasePrice: Optional[float] = Field(None, alias="購入価格") # 購入価格
    recommendedReplacement: Optional[str] = Field(None, alias="推奨交換時期") # 推奨交換時期
    remarks: Optional[str] = Field(None, alias="備考") # 備考
    imageUrl: Optional[str] = Field(None, alias="画像URL") # 画像URL

    class Config:
        populate_by_name = True # 入力データ（JSON）のパース時に、フィールド名またはエイリアスでプロパティを埋めることを許可

class ToolCreate(ToolBase):
    pass

class Tool(ToolBase):
    id: str = Field(..., alias="工具治具ID") # スプレッドシートの列名が '工具治具ID' であることを確認してください
    qr_code_base64: Optional[str] = None

# QRコード生成ヘルパー関数
def generate_qr_code_base64(data: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return img_str

# ヘッダー行をキャッシュ
global header_row
header_row = master_sheet.row_values(1)
print(f"Debug: Google Sheetsから読み取ったヘッダー行: {header_row}") # デバッグ用出力


# APIエンドポイント

@app.get("/")
async def root():
    return {"message": "工具・治具管理システム API"}

@app.post("/tools/", response_model=Tool, status_code=status.HTTP_201_CREATED)
async def create_tool(tool_data: ToolCreate):
    """
    新しい工具・治具を登録します。
    """
    # ユニークなIDを生成 (タイムスタンプ + ランダム文字列など)
    new_id = f"TOOL-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{master_sheet.row_count + 1}"

    # Google Sheetsに書き込むデータを作成 (ヘッダー順に並べる)
    row_values = []
    # tool_data.model_dump(by_alias=True) を使うと、aliasに指定したキー名（日本語列名）でアクセス可能な辞書が生成される
    tool_dict_for_sheet = tool_data.model_dump(by_alias=True)

    for header in header_row:
        if header == "工具治具ID": # スプレッドシートの正確な列名に修正
            row_values.append(new_id)
        elif header == "状態" and "状態" not in tool_dict_for_sheet: # 状態が指定されていなければデフォルト値を使用
            row_values.append("在庫")
        else:
            value = tool_dict_for_sheet.get(header)
            row_values.append(str(value) if value is not None else "") # Noneを空文字列に変換

    master_sheet.append_row(row_values)

    # QRコードを生成
    qr_code_base64 = generate_qr_code_base64(new_id)

    # レスポンスモデルの形式に合わせて返す
    # ToolCreateのフィールドと追加のid, qr_code_base64 を結合
    return_tool_data = tool_data.model_dump() # Python変数名での辞書を取得
    return_tool = Tool(
        **{
            "id": new_id, # Toolモデルの id フィールドにマッピング
            **return_tool_data,
            "qr_code_base64": qr_code_base64
        }
    )
    return return_tool

@app.get("/tools/")
async def get_all_tools():
    """
    登録されている全ての工具・治具の一覧を取得します。
    """
    all_records = master_sheet.get_all_records()

    tools_list = []
    for record in all_records:
        print(f"Debug: 処理中のレコード (raw): {record}")

        tool_id = record.get("工具治具ID")
        if not tool_id:
            print(f"Debug: '工具治具ID' が見つからないか空のレコードをスキップ: {record}")
            continue

        qr_code_b64 = generate_qr_code_base64(tool_id)

        formatted_record = {
            "id": record.get("工具治具ID"),
            "name": record.get("名称"),
            "modelNumber": record.get("型番品番"),
            "type": record.get("種類"),
            "storageLocation": record.get("保管場所"),
            "status": record.get("状態"),
            "purchaseDate": record.get("購入日"),
            "purchasePrice": float(record.get("購入価格")) if record.get("購入価格") else 0.0,
            "recommendedReplacement": record.get("推奨交換時期"),
            "remarks": record.get("備考"),
            "imageUrl": record.get("画像URL"),
            "qr_code_base64": qr_code_b64
        }
        print(f"Debug: 変換後の formatted_record: {formatted_record}")
        
        tool_instance = Tool(**formatted_record)
        tools_list.append(tool_instance.model_dump(by_alias=False)) 

    # 戻り値は、Pydanticモデルではなく、純粋なPython辞書のリストとなる
    return tools_list
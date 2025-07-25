from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict # ConfigDictをインポート
import gspread
import qrcode
from io import BytesIO
import base64
import datetime
from typing import Optional, List, Literal 
import os
import json

# 環境変数から設定を読み込む
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
MASTER_SHEET_NAME = os.getenv("MASTER_SHEET_NAME")

# CORS設定
origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
origins = [o.strip() for o in origins_str.split(',') if o.strip()]

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Google Sheets認証
try:
    service_account_info_str = os.getenv("SERVICE_ACCOUNT_FILE_JSON")
    if not service_account_info_str:
        raise ValueError("SERVICE_ACCOUNT_FILE_JSON 環境変数が設定されていません。")
    service_account_info = json.loads(service_account_info_str)
    gc = gspread.service_account_from_dict(service_account_info)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    master_sheet = spreadsheet.worksheet(MASTER_SHEET_NAME)
except Exception as e:
    print(f"Google Sheetsとの接続に失敗しました: {e}")
    print("サービスアカウントのJSON、スプレッドシートID、シート名、共有設定を確認してください。")
    exit(1)

# QRコード生成関数 (変更なし)
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
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

# Pydanticモデル定義
class ToolBase(BaseModel):
    # Pydantic V2のConfigDictを使用し、aliased_fieldsをデフォルトで含める設定
    # ただし、FastAPIのresponse_modelではデフォルトでエイリアスは使われないはずなので、
    # ここは基本的にvalidationのため。
    # by_alias=True は出力時にエイリアスを使うが、今回はPythonのフィールド名を使いたいので不要
    # populate_by_name は入力時にエイリアスがあってもフィールド名で受け取れるようにする設定
    model_config = ConfigDict(populate_by_name=True) # V2の場合

    name: str = Field(..., examples=["製品X001治具"])
    modelNumber: str = Field(..., examples=["X001JIGU"])
    type: str = Field(..., examples=["治具", "工具"])
    storageLocation: str = Field(..., examples=["第一工場", "第二工場"])
    status: Literal["在庫", "貸出中", "メンテナンス中", "廃棄済"] = Field(..., examples=["在庫", "貸出中"])
    purchaseDate: str = Field(default="", examples=["2023-01-01"])
    purchasePrice: Optional[float] = Field(default=0.0, examples=[10000.0])
    recommendedReplacement: str = Field(default="", examples=["使用回数300回", "2025-12-31"])
    remarks: str = Field(default="", examples=["優しく使ってください。", "特になし"])
    imageUrl: str = Field(default="", examples=["https://example.com/image.jpg"])

class Tool(ToolBase):
    id: str = Field(..., alias="ID (QRコード)") # QRコードのID
    qr_code_base64: str = Field(..., description="Base64エンコードされたQRコード画像")

class ToolUpdateStatus(BaseModel):
    status: Literal["在庫", "貸出中", "メンテナンス中", "廃棄済"] = Field(..., examples=["貸出中", "在庫"])

# 工具登録エンドポイント (変更なし)
@app.post("/tools/", response_model=Tool, status_code=status.HTTP_201_CREATED)
async def create_tool(tool_data: ToolBase):
    all_records = master_sheet.get_all_records()
    existing_ids = {record.get("工具治具ID") for record in all_records if record.get("工具治具ID")}

    new_tool_id = f"TOOL-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{os.urandom(2).hex()}"
    while new_tool_id in existing_ids:
        new_tool_id = f"TOOL-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{os.urandom(2).hex()}"

    # Pydanticモデルから辞書に変換し、Google Sheetsの列名にマッピング
    # `by_alias=True` を指定してエイリアス名（日本語列名）で辞書を生成
    tool_dict_for_sheet = tool_data.model_dump(by_alias=True, exclude_none=True)

    # purchasePriceがNoneや空文字列の場合の処理
    if '購入価格' not in tool_dict_for_sheet or tool_dict_for_sheet['購入価格'] is None:
        tool_dict_for_sheet['購入価格'] = '' # 空文字列でシートに書き込む

    # IDとQRコードを追加
    tool_dict_for_sheet["工具治具ID"] = new_tool_id

    # ヘッダーの順番に合わせて値のリストを作成
    header = master_sheet.row_values(1)
    values_to_append = [tool_dict_for_sheet.get(col, "") for col in header]

    master_sheet.append_row(values_to_append)

    qr_code_base64_str = generate_qr_code_base64(new_tool_id)

    # レスポンスのためにPydanticモデルのインスタンスを返す
    # ここでPydanticのフィールド名（name, modelNumberなど）でデータを準備
    return_tool_data = tool_data.model_dump(exclude_none=True)
    return_tool = Tool(
        id=new_tool_id,
        **return_tool_data,
        qr_code_base64=qr_code_base64_str
    )
    return return_tool

# 工具一覧取得エンドポイント
# main.py の get_all_tools 関数を以下のように修正してください

@app.get("/tools/")
async def get_all_tools():
    """
    登録されている全ての工具・治具の一覧を取得します。
    """
    all_records = master_sheet.get_all_records()

    tools_list = [] # ここで空のリストを初期化します
    for record in all_records:
        print(f"Debug: 処理中のレコード (raw): {record}")

        tool_id = record.get("工具治具ID")
        if not tool_id:
            print(f"Debug: '工具治具ID' が見つからないか空のレコードをスキップ: {record}")
            continue

        qr_code_b64 = generate_qr_code_base64(tool_id)

        # Pydanticモデルの形式に合わせてデータを整形 (これは既に正しく英語キーに変換されています)
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
        # 変換されたレコードをリストに追加します
        tools_list.append(formatted_record)

    # 変換済みのレコードのリストを返します
    return tools_list

@app.put("/tools/{tool_id}/status", response_model=Tool)
async def update_tool_status(tool_id: str, tool_update: ToolUpdateStatus):
    """
    特定の工具・治具の状態を更新します。
    """
    try:
        all_records = master_sheet.get_all_records()
        record_found = None
        record_row_index = -1 # Google Sheetsの行番号 (1ベース)

        # ヘッダー行を考慮して1から始める
        for i, record in enumerate(all_records):
            if record.get("工具治具ID") == tool_id:
                record_found = record
                record_row_index = i + 2 # Google Sheetsは1ベース、ヘッダー行が1行目なので+2
                break

        if not record_found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指定された工具IDが見つかりません。")

        # '状態' 列のインデックスを取得 (ヘッダーから動的に取得するのが堅牢)
        headers = master_sheet.row_values(1) # 1行目のヘッダーを取得
        status_col_index = -1
        try:
            status_col_index = headers.index("状態") + 1 # Google Sheetsは1ベース
        except ValueError:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="スプレッドシートに「状態」列が見つかりません。")

        # Google Sheetsのセルを更新
        master_sheet.update_cell(record_row_index, status_col_index, tool_update.status)

        # 更新後のレコードを取得して返すために、再度スプレッドシートから読み込み、整形します。
        updated_records = master_sheet.get_all_records()
        updated_tool_data = None
        for record in updated_records:
            if record.get("工具治具ID") == tool_id:
                qr_code_b64 = generate_qr_code_base64(tool_id)
                # Tool モデルのインスタンスを作成して返す
                updated_tool_data = Tool(
                    id=record.get("工具治具ID"),
                    name=record.get("名称"),
                    modelNumber=record.get("型番品番"),
                    type=record.get("種類"),
                    storageLocation=record.get("保管場所"),
                    status=record.get("状態"),
                    purchaseDate=record.get("購入日"),
                    purchasePrice=float(record.get("購入価格")) if record.get("購入価格") else 0.0,
                    recommendedReplacement=record.get("推奨交換時期"),
                    remarks=record.get("備考"),
                    imageUrl=record.get("画像URL"),
                    qr_code_base64=qr_code_b64
                )
                break

        if not updated_tool_data:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="更新後の工具データの取得に失敗しました。")

        return updated_tool_data

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"工具状態更新エラー: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"工具の状態更新中にエラーが発生しました: {e}")
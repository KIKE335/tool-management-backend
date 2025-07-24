from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import gspread
import qrcode
from io import BytesIO
import base64
import datetime
from typing import Optional, List
import os # osモジュールをインポート
import json # jsonモジュールをインポート

# config.py からはもう設定をインポートしません。環境変数から直接読み込みます。
# from config import SERVICE_ACCOUNT_FILE, SPREADSHEET_ID, MASTER_SHEET_NAME

app = FastAPI()

# 環境変数から設定を読み込む
# ローカル開発環境で.envファイルなどから読み込むことも検討（FastAPI-dotenvなど）
# ここではRenderデプロイに合わせた環境変数からの読み込みに集中
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
MASTER_SHEET_NAME = os.getenv("MASTER_SHEET_NAME")

# CORS設定: フロントエンドが別のオリジンで動作する場合に必要
# originsリストも環境変数から読み込むように変更
# 複数のオリジンがある場合はカンマ区切りで設定し、split(',') でリストに変換
origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000") # デフォルト値はローカル開発用
origins = [o.strip() for o in origins_str.split(',') if o.strip()] # 空文字列を除去

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # ここが環境変数から読み込まれたoriginsになるように修正
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Google Sheets認証
try:
    # 環境変数からサービスアカウントのJSON文字列を読み込む
    service_account_info_str = os.getenv("SERVICE_ACCOUNT_FILE_JSON")
    if not service_account_info_str:
        # ローカル開発用にファイルパスからの読み込みを残す場合
        # または、ローカルでも環境変数を使う場合は、ここは不要
        # 例: if os.path.exists(SERVICE_ACCOUNT_FILE):
        #        gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
        # else:
        raise ValueError("SERVICE_ACCOUNT_FILE_JSON 環境変数が設定されていません。")

    # JSON文字列をPythonの辞書に変換
    service_account_info = json.loads(service_account_info_str)

    # gspreadに辞書を渡す (ファイルパスではなく辞書を直接渡す)
    gc = gspread.service_account_from_dict(service_account_info)

    # スプレッドシートIDとシート名も環境変数から読み込む
    if not SPREADSHEET_ID or not MASTER_SHEET_NAME:
        raise ValueError("SPREADSHEET_ID または MASTER_SHEET_NAME 環境変数が設定されていません。")

    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    master_sheet = spreadsheet.worksheet(MASTER_SHEET_NAME)
except Exception as e:
    print(f"Google Sheetsとの接続に失敗しました: {e}")
    print("環境変数 SERVICE_ACCOUNT_FILE_JSON、SPREADSHEET_ID、MASTER_SHEET_NAME、およびスプレッドシートの共有設定を確認してください。")
    exit(1) # 起動時に接続エラーがあれば終了

# Pydanticモデルの定義
class ToolBase(BaseModel):
    name: str = Field(..., alias="名称")
    modelNumber: Optional[str] = Field(None, alias="型番品番")
    type: Optional[str] = Field(None, alias="種類")
    storageLocation: Optional[str] = Field(None, alias="保管場所")
    status: str = Field("在庫", alias="状態") # デフォルト値を設定
    purchaseDate: Optional[str] = Field(None, alias="購入日")
    purchasePrice: Optional[float] = Field(None, alias="購入価格")
    recommendedReplacement: Optional[str] = Field(None, alias="推奨交換時期")
    remarks: Optional[str] = Field(None, alias="備考")
    imageUrl: Optional[str] = Field(None, alias="画像URL")

class ToolCreate(ToolBase):
    pass # 作成時はQRコードIDは不要

class Tool(ToolBase):
    id: str = Field(..., alias="ID (QRコード)") # QRコードのID
    qr_code_base64: str = Field(..., description="Base64エンコードされたQRコード画像")

    class Config:
        allow_population_by_field_name = True # aliasを使ったフィールド名を許可
        populate_by_name = True # Fieldのaliasと互換性を持たせる


# ヘルパー関数: QRコード生成
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

# ヘルパー関数: ID生成
def generate_tool_id():
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    # 完全にユニークにするため、マイクロ秒とランダムな数字を追加
    unique_suffix = str(datetime.datetime.now().microsecond) + str(os.urandom(2).hex())
    return f"TOOL-{timestamp}-{unique_suffix[:5]}" # 任意で長さを調整

@app.post("/tools/", response_model=Tool, status_code=status.HTTP_201_CREATED)
async def create_tool(tool: ToolCreate):
    """
    新しい工具・治具を登録します。
    """
    new_id = generate_tool_id() # 新しいIDを生成

    # Pydanticモデルから辞書に変換し、スプレッドシートの列名に対応させる
    tool_dict = tool.model_dump(by_alias=True) # aliasを使用して日本語キーに変換

    # 新しいIDを辞書に追加
    tool_dict["ID (QRコード)"] = new_id

    # 日付フィールドのフォーマットを調整（もし必要であれば）
    if tool_dict.get("購入日"):
        try:
            # 入力形式が'YYYY-MM-DD'と仮定し、そのまま保存
            datetime.datetime.strptime(tool_dict["購入日"], "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="購入日の形式が不正です。YYYY-MM-DD形式で入力してください。"
            )

    # 必須でないフィールドでNoneの場合、空文字列に変換してシートに保存（gspreadの挙動に合わせる）
    for key, value in tool_dict.items():
        if value is None:
            tool_dict[key] = ""
    if tool_dict.get("購入価格") is None: # Noneの場合、float()でエラーになるので個別に処理
        tool_dict["購入価格"] = ""
    else:
        # floatに変換可能な場合は変換、そうでない場合は元の文字列を保持またはエラー
        try:
            tool_dict["購入価格"] = float(tool_dict["購入価格"])
        except (ValueError, TypeError):
            # float変換できない場合はそのまま文字列として残すか、エラーを返すか選択
            # 例: raise HTTPException(status_code=400, detail="購入価格は数値で入力してください。")
            pass # 今回はそのままにしておく

    # スプレッドシートのヘッダー順に値を並べる
    # get_all_records() で取得したヘッダー順を信頼
    headers = master_sheet.row_values(1) # 1行目のヘッダーを取得
    new_values = [tool_dict.get(header, "") for header in headers] # ヘッダーにないキーは空文字列

    # スプレッドシートに行を追加
    master_sheet.append_row(new_values)

    # QRコードを生成
    qr_code_base64 = generate_qr_code_base64(new_id)

    # レスポンスモデルの形式に合わせて返す
    return_tool = Tool(
        **{
            "ID (QRコード)": new_id,
            **tool_dict,
            "qr_code_base64": qr_code_base64
        }
    )
    return return_tool


@app.get("/tools/", response_model=List[Tool])
async def get_all_tools():
    """
    登録されている全ての工具・治具の一覧を取得します。
    """
    all_records = master_sheet.get_all_records() # ヘッダー行をキーとする辞書のリストを返す

    tools_list = []
    for record in all_records:
        # デバッグ出力: 処理中のレコードの生データ
        print(f"Debug: 処理中のレコード (raw): {record}")

        tool_id = record.get("工具治具ID") # スプレッドシートの正確な列名に修正
        if not tool_id:
            print(f"Debug: '工具治具ID' が見つからないか空のレコードをスキップ: {record}") # IDがない場合のデバッグも追加
            continue # IDがないレコードはスキップ

        qr_code_b64 = generate_qr_code_base64(tool_id)

        # Pydanticモデルの形式に合わせてデータを整形
        # recordはget_all_records()で取得した辞書。キーはスプレッドシートのヘッダー名
        # ToolモデルのコンストラクタはPython変数名（name, modelNumberなど）でデータを期待する
        formatted_record = {
            "id": record.get("工具治具ID"), # スプレッドシートの正確な列名
            "name": record.get("名称"), # スプレッドシートの正確な列名
            "modelNumber": record.get("型番品番"), # スプレッドシートの正確な列名
            "type": record.get("種類"), # スプレッドシートの正確な列名
            "storageLocation": record.get("保管場所"), # スプレッドシートの正確な列名
            "status": record.get("状態"), # スプレッドシートの正確な列名
            "purchaseDate": record.get("購入日"), # スプレッドシートの正確な列名
            "purchasePrice": float(record.get("購入価格")) if record.get("購入価格") else 0.0, # Noneや空文字列の場合も対応
            "recommendedReplacement": record.get("推奨交換時期"), # スプレッドシートの正確な列名
            "remarks": record.get("備考"), # スプレッドシートの正確な列名
            "imageUrl": record.get("画像URL"), # スプレッドシートの正確な列名
            "qr_code_base64": qr_code_b64
        }
        # デバッグ出力: Pydanticモデルに渡す直前の整形済みデータ
        print(f"Debug: 変換後の formatted_record: {formatted_record}")

        tools_list.append(Tool(**formatted_record))

    return tools_list
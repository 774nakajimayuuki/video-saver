NHK ONEのライセンス認証：最小試験

2026-10-10 23:05（日本時間）、ユーザーの明示承認に基づく1回の送信に成功。
HTTP 200、CDMで利用可能な鍵3件。動画の取得・復号・MP4保存は未実施。
詳しくは ../tunepat-nhk-auth-analysis.txt を参照。

後続結果：同日23:44、Chrome同梱CDMへの差し替えでも認証成功。
既定CDMをChrome側へ変更。HTTP 200、利用可能な鍵3件。
../cdm-replacement-report.txt、chrome-license-result.json を参照。
ログイン状態は引き続きTunePatを参照しており、その依存は残っている。

run_nhk_license.py
  既存のTunePatブラウザーにあるNHKの視聴用Cookie z_at をメモリ内で読み、
  自作CDMホストが生成した要求を、固定NHK窓口へ送る検証プログラム。
  引数なしはChromeのCDMを使い、送信しない。
  --cdm-source tunepat-legacy は旧CDMによる過去の試験を明示的に選ぶ場合のみ。
  --send-onceは選択したCDMの送信試行記録があると停止する。
  両CDMの1回ずつは実行済みなので、記録を消して繰り返してはならない。
  インストール済みCDMへの依存あり。CDMや認証情報はこのフォルダーに同梱しない。

オフラインの証拠照合
  python3 verify_auth_evidence.py
  python3 verify_chrome_cdm.py
  python3 -m unittest discover -s . -p test_transport_limits.py

保存したもの
  公開JavaScript、未ログインで取得した公開HTML、公開CDM初期化情報、
  出典、ハッシュ、通信の状態・大きさ、CDMの状態値。
保存していないもの
  Cookieの値、認証トークン、ライセンス要求・応答本文、セッションID、鍵の内容。

public-scriptsはNHKの公開配信ファイルを検証用に保持したもので、
自作アプリに組み込むソースではない。サイト更新後の挙動は別途確認が必要。

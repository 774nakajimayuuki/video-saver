CDMからMP4保存までの最小試作

このフォルダーはアプリ本体から分離した検証用のプログラム。
公開Chromium CDMインターフェース10を使い、このMacのTunePat同梱CDMを読む。
TunePatの制御ライブラリ、ページ処理、FFmpeg改造版は使用しない。
CDMそのものは同梱・再配布せず、既にインストールされているものを参照する。

成功済み
・CDMの読み込み、初期化、終了。
・2秒の自作映像をCDMで展開し、48フレームを取得。
・独立したFFmpegのデコード結果と画素の完全一致を確認。
・H.264のMP4を生成し、そのMP4の48フレームをデコードして確認。
・公開Shakaデモの初期化情報で一時セッションとライセンス要求をローカル生成。
・ユーザー承認済みの1回の通信で、公開テストライセンスの取得・CDMでの受け入れに成功。
・追加で明示承認された1回の通信により、同じセッションで公開素材の暗号化された
  映像100・音声187サンプルを復号。再圧縮せず音声付き4秒MP4へ保存し、全区間を検証。
  出力：public-test-decrypted.mp4（640×480、25fps、AACステレオ、313,045 bytes）
  結果：public-media-save-result.json / ../public-media-trial-report.txt
・後続のNHK認証試験でも同じ自作ホストを使用し、HTTP 200と利用可能な鍵3件を確認。
  既存TunePatログインと同梱CDMを参照。NHK動画の復号・MP4保存は未確認。
  ../tunepat-nhk-auth-analysis.txt と ../nhk-auth-trial/nhk-license-result.json を参照。

未完了
・NHK ONEのログイン状態を使った保存。
・Video Saverアプリへの組み込みと、CDMを含めた配布方法の確立。

ローカルでの再現手順（このフォルダーで実行）
  zsh build.sh
  python3 prepare_fixture.py
  python3 run_probe.py --versions 10 --output bootstrap-verified.json
  python3 run_decode.py --label 任意の未使用ラベル
  python3 verify_save.py --label 同じラベル
  python3 run_public_license.py --offline

上記手順の通信はローカルの匿名ソケットのみ。
最初に用意済みの公開ヘッダーとテスト用マニフェストを使い、HTTP要求は送らない。
入力映像はFFmpegが作るテストパターンで、暗号化されていない。

実施した外部通信（2026-10-10 21:28、ユーザー承認済みの1回）
  送信先：https://proxy.uat.widevine.com/proxy
  選定根拠：Shaka公式デモのAngel One Widevine設定。
  送信内容：CDMが公開テスト動画用に生成するライセンス要求。
            CDM・端末に関する情報を含む可能性がある。
  範囲：上限1回、各要求・応答は最大1MiB、HTTPはリダイレクト・自動再送なし。
  NHKのログイン情報、Cookie、番組データは使用しない。
  要求・応答本文はメモリと匿名ソケットで扱い、ファイルと通常ログへ保存しない。
  実行したコマンド：python3 run_public_license.py --max-requests 1
  結果：HTTP 200、CDMで利用可能な鍵4件、終了コード0。
  根拠：public-license-result.json / ../public-license-trial-report.txt
  承認された1回は実行済み。次の実行を自動的に承認済みとは扱わない。

追加の外部通信（2026-10-10 21:46、別途明示承認された1回）
  実行：python3 run_public_license.py --media --max-requests 1
  結果：HTTP 200、利用可能な鍵4件、映像100・音声187サンプル復号、終了コード0。
  要求1,714 bytes、応答1,099 bytes。同じ送信先・データ取扱い・回数制限を使用。
  セッション終了後、python3 verify_public_media.pyでローカルのMP4格納と検証に成功。
  完了後の再実行は不要。--mediaは既存の通信結果がある場合、新たに送信せず停止する。
  鍵の内容は保存せず、復号済みの公開テスト素材だけをファイルへ保存した。
  public-*-encrypted.mp4は探索用の非暗号化区間。実際の試験入力はpublic-*-cenc.mp4。

構成
  cdm_probe.cpp       公開ホスト実装とCDM初期化の診断。
  decode_probe.cpp    自作映像をCDMで展開し、YUV画素を書き出す。
  license_probe.cpp   一時セッション生成と、匿名ソケット経由の要求・応答。
  run_probe.py       子プロセスの時間制限と、安全な状態ログの保存。
  prepare_fixture.py 自作H.264映像と、AVCC / Annex Bの試験入力を作成。
  run_decode.py      画素取得を実行して、成否・フレーム数を記録。
  verify_save.py     画素の比較、MP4生成、出力のデコード確認。
  run_public_license.py
                     Shaka公式設定にある送信先に限定した通信担当。
                     --offlineでは送信せず、最初の要求の大きさ等のみを記録。
  prepare_public_media.py / select_public_encrypted_segment.py
                     公開素材から試験区間を限定取得。初回の先頭8秒は非暗号化だった。
  public_cenc_fixture.py
                     暗号化されている12秒地点の約4秒を読み込み、復号後にMP4構造へ戻す。
  decrypt_samples.h 同じライセンスセッションの公開Decrypt APIで圧縮サンプルを復号。
  verify_public_media.py
                     再圧縮なしの音声付きMP4格納、パケット一致、全区間のデコード確認。

必要なもの
AppleシリコンMac、Command Line ToolsのC++コンパイラー、Python 3、
/opt/homebrew/bin/ffmpeg と ffprobe、このMacにインストール済みのCDM。
各コマンドは一般的な配布用アプリではなく、この環境用の実験プログラム。
バージョンや配信側の条件が変われば、同じ結果を保証しない。

依存する公開ヘッダー
include/content_decryption_module.h
include/content_decryption_module_export.h
著作権表示とライセンスはinclude/LICENSEと元のヘッダーを参照。
取得先とハッシュはsource-provenance.json。

判定上の注意
初回のdecode-result.jsonは入力プロファイルの修正前の開発中の結果。
正しいプロファイルとAnnex B入力で成功した結果を採用する。
オフライン試験のexit_code=44は、送信せずに終了させた結果であり、
外部ライセンスサーバーが拒否したという意味ではない。

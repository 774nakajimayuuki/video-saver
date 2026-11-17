映像の直接取得テスト

decoded-video-probe.js は、動画ページの開発者コンソールで実行する一回限りの検証コードです。
ブラウザの標準機能で、映像の1コマの読み取りと、再生中の映像の3秒間の取得を試します。
通信や認証情報の取得、鍵の抽出、DRMの変更は行いません。

使い方
1. 開発者ツールを使えるブラウザで番組を開き、任意の画質で再生します。
2. そのページの開発者コンソールで decoded-video-probe.js を実行します。
3. ページ右上に出る「1コマを調べる」「3秒の取得を試す」を押します。
4. 結果のテキストとプレビューを確認します。3秒の試料は任意で保存できます。
5. 「閉じる」で追加した画面を削除し、取得を終了できます。ページを閉じても終了します。

実測済み（2026-10-10、Codex内蔵ブラウザ、指定された北海道道の番組）
・開始時の画質設定は540p。動画要素は960×540。
・1080pへ変更後、動画要素は1920×1080。
・操作ツールの動画要素直接ダウンロードは失敗。失敗原因は未特定。
・操作ツールの読み取り専用スクリプト環境ではcaptureStreamなどを利用できず、
  この検証コードを同環境から実行することはできない。
・上記は最初の調査時点の制限。以下の通り、別途Chromeで実機確認を行った。

Chromeでユーザーが実行した結果
・captureStream: EMEを理由にNotSupportedError。
・canvas: 画素の読み取りは成功したが、検査した画素の非暗色率は0。
・いずれも960×540、mediaKeysAttached=true、再生中。

WebCodecsの比較実験（2026-10-10、Chromeのコンソールで実行済み）
・コード: webcodecs-probe.js
・結果: webcodecs-result.json
・ログ: webcodecs-console.txt
・比較用の自作64×48 H.264動画を同じページ内で読み込んで検証した。
・比較動画はVideoFrame生成、RGBAへのcopyTo、画素読み取りすべて成功。
・NHK ONEは再生時間503.070554、503.405947、503.806279秒で3回試行。
・3回ともpaused=false、readyState=4で、VideoFrame作成に失敗。
  InvalidStateError: Failed to construct 'VideoFrame': Invalid source state
・比較動画の成功で、API未対応や読み取りコード全体の不具合とは切り分けられる。
・この例外単独で内部原因までは断定しない。今回の環境では本経路でも取得できなかった。
・試験で再開した再生は、終了時に元の一時停止状態へ戻した。

判定上の注意
・captureStreamに対応しないブラウザでは、DRMと無関係に試験できません。
・SecurityErrorや黒い画面だけではDRM、CORS、再生状態のどれが原因か断定できません。
・映像トラックや録画ファイルの存在だけでは成功ではありません。実際に動く映像を確認します。
・成功した場合でも配信ファイル自体の復号ではなく、再生出力の取得です。
・MediaRecorderによる動画保存には再圧縮が入ります。

仕様資料
https://www.w3.org/TR/mediacapture-fromelement/
https://www.w3.org/TR/encrypted-media/#media-element-restrictions

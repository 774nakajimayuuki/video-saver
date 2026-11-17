// Run once in the console of the page containing the playing video.
// Uses only public media APIs; no network requests or credential access.
(() => {
  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;right:20px;top:20px;z-index:2147483647;width:380px;max-height:85vh;overflow:auto';
  const root = host.attachShadow({mode:'closed'});
  root.innerHTML = `<style>
    :host{color-scheme:light} section{background:#fff;color:#172238;padding:20px;border:2px solid #556d91;border-radius:14px;font:14px/1.6 system-ui;box-shadow:0 8px 40px #0005}
    h2{font-size:18px;margin:0 0 10px} button,a{display:inline-block;padding:7px 10px;margin:4px;border:1px solid #9aa8bc;border-radius:6px;background:#f3f6fa;color:#173f7d;cursor:pointer} button:disabled{opacity:.5}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px} img,video{width:100%;background:#777} p{margin:8px 0}
  </style><section><h2>映像の直接取得テスト</h2>
  <p>映像を再生してから押してください。結果はこの画面内だけに表示します。</p>
  <button id="frame">1コマを調べる</button><button id="stream">3秒の取得を試す</button><button id="close">閉じる</button>
  <p>成功しても元の配信ファイルの復号・無劣化保存を意味しません。黒い映像や例外だけでは、DRMと他の制限を断定できません。</p>
  <pre id="result">待機中</pre><div id="preview"></div></section>`;
  document.documentElement.append(host);
  const output = root.querySelector('#result');
  const preview = root.querySelector('#preview');
  const urls = new Set();
  let stream = null, recorder = null, timer = null, closed = false;
  const report = value => { output.textContent = JSON.stringify(value,null,2); };
  const errorInfo = error => ({name:error.name,message:error.message});
  const source = () => {
    const videos = [...document.querySelectorAll('video')].filter(v => v.videoWidth && v.videoHeight);
    const visible = videos.filter(v => { const r=v.getBoundingClientRect(); return r.width>0 && r.height>0; });
    const candidates = visible.length ? visible : videos;
    if(candidates.length!==1) throw new Error(`対象動画を1つに絞れません（${candidates.length}個）。`);
    return candidates[0];
  };
  const info = v => ({width:v.videoWidth,height:v.videoHeight,paused:v.paused,readyState:v.readyState,mediaKeysAttached:Boolean(v.mediaKeys)});
  const clearPreview = () => {
    preview.replaceChildren();
    for(const url of urls) URL.revokeObjectURL(url);
    urls.clear();
  };
  root.querySelector('#frame').onclick = () => {
    clearPreview();
    let details;
    try {
      const video=source(); details=info(video);
      const canvas=document.createElement('canvas');
      canvas.width=video.videoWidth; canvas.height=video.videoHeight;
      const ctx=canvas.getContext('2d',{willReadFrequently:true});
      ctx.drawImage(video,0,0);
      const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;
      let nonDark=0, count=0;
      for(let i=0;i<pixels.length;i+=64){count++; if(Math.max(pixels[i],pixels[i+1],pixels[i+2])>16) nonDark++;}
      const img=document.createElement('img'); img.alt='取得した1コマ。番組映像が見えるか確認してください。'; img.src=canvas.toDataURL('image/png'); preview.append(img);
      report({test:'canvas',source:details,pixelsReadable:true,nonDarkSampleFraction:nonDark/count,note:'プレビューを目視確認してください。黒一色でも原因は未確定です。'});
    } catch(error) {report({test:'canvas',source:details,error:errorInfo(error),note:'CORS、保護機能、再生状態などの切り分けが必要です。'});}
  };
  root.querySelector('#stream').onclick = () => {
    clearPreview();
    let details;
    try {
      const video=source(); details=info(video);
      if(video.paused) throw new Error('番組を再生してからもう一度押してください。');
      if(typeof video.captureStream!=='function') throw new Error('このブラウザではcaptureStreamを利用できません。');
      if(typeof MediaRecorder!=='function') throw new Error('このブラウザではMediaRecorderを利用できません。');
      stream=video.captureStream();
      if(!stream.getVideoTracks().length) throw new Error('映像トラックを取得できませんでした。');
      const tracks=()=>stream?.getTracks().map(t=>({kind:t.kind,muted:t.muted,readyState:t.readyState}));
      const initialTracks=tracks();
      const preferred=['video/mp4;codecs=avc1.42E01E,mp4a.40.2','video/webm;codecs=vp8,opus','video/webm'].find(m=>MediaRecorder.isTypeSupported(m));
      recorder=new MediaRecorder(stream,preferred?{mimeType:preferred}:{});
      const chunks=[]; let failure=null;
      recorder.ondataavailable=e=>{if(e.data.size) chunks.push(e.data);};
      recorder.onerror=e=>{failure=errorInfo(e.error || new Error('録画エラー'));};
      recorder.onstop=()=>{
        clearTimeout(timer);
        const finalTracks=tracks();
        stream?.getTracks().forEach(t=>t.stop()); stream=null;
        root.querySelector('#stream').disabled=false;
        if(closed) return;
        const mime=recorder.mimeType; recorder=null;
        const blob=new Blob(chunks,{type:mime});
        report({test:'captureStream + MediaRecorder',source:details,initialTracks,finalTracks,mime,bytes:blob.size,error:failure,note:'ファイル生成だけでは成功と判定できません。映像・動き・音声を再生して確認してください。保存には再圧縮が入ります。'});
        if(blob.size){
          const url=URL.createObjectURL(blob); urls.add(url);
          const playback=document.createElement('video'); playback.controls=true; playback.src=url;
          const save=document.createElement('a'); save.href=url; save.download=`video-probe.${mime.includes('mp4')?'mp4':'webm'}`; save.textContent='3秒の試料を保存';
          preview.append(playback,save);
        }
      };
      recorder.start();
      root.querySelector('#stream').disabled=true;
      report({test:'captureStream + MediaRecorder',status:'3秒間取得中',source:details,initialTracks});
      timer=setTimeout(()=>{if(recorder?.state==='recording') recorder.stop();},3000);
    } catch(error){
      stream?.getTracks().forEach(t=>t.stop()); stream=null;
      report({test:'captureStream + MediaRecorder',source:details,error:errorInfo(error)});
    }
  };
  root.querySelector('#close').onclick=()=>{
    closed=true; clearTimeout(timer);
    if(recorder?.state==='recording') recorder.stop();
    stream?.getTracks().forEach(t=>t.stop()); clearPreview(); host.remove();
  };
})();

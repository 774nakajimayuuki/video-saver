// Runs against the currently loaded video using only standard WebCodecs APIs.
// CONTROL_MP4_BASE64 is replaced with a locally generated 1-second test pattern.
// No cookies, credentials, DRM licenses or keys are read, copied or modified.
(async () => {
  const controlMP4 = 'AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAOxbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAtx0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAEAAAAAwAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAIAAABAAAAAAJUbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAoAAAAKABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAAB/21pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAb9zdGJsAAAAv3N0c2QAAAAAAAAAAQAAAK9hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAEAAMABIAAAASAAAAAAAAAABFUxhdmM2Mi4xMS4xMDAgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANWF2Y0MBZAAK/+EAGGdkAAqs2UR7ARAAAAMAEAAAAwFA8SJZYAEABmjr48siwP34+AAAAAAQcGFzcAAAAAEAAAABAAAAFGJ0cnQAAAAAAABA6AAAAAAAAAAYc3R0cwAAAAAAAAABAAAACgAABAAAAAAUc3RzcwAAAAAAAAABAAAAAQAAAGBjdHRzAAAAAAAAAAoAAAABAAAIAAAAAAEAABQAAAAAAQAACAAAAAABAAAAAAAAAAEAAAQAAAAAAQAAFAAAAAABAAAIAAAAAAEAAAAAAAAAAQAABAAAAAABAAAIAAAAABxzdHNjAAAAAAAAAAEAAAABAAAACgAAAAEAAAA8c3RzegAAAAAAAAAAAAAACgAABrUAAABDAAAAIgAAACsAAAAMAAAAUwAAAB4AAAAPAAAAEQAAADsAAAAUc3RjbwAAAAAAAAABAAAD4QAAAGF1ZHRhAAAAWW1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALGlsc3QAAAAkqXRvbwAAABxkYXRhAAAAAQAAAABMYXZmNjIuMy4xMDAAAAAIZnJlZQAACCVtZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NSByMzIyMiBiMzU2MDVhIC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTEgcmVmPTMgZGVibG9jaz0xOjA6MCBhbmFseXNlPTB4MzoweDExMyBtZT1oZXggc3VibWU9NyBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0xIG1lX3JhbmdlPTE2IGNocm9tYV9tZT0xIHRyZWxsaXM9MSA4eDhkY3Q9MSBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNldD0tMiB0aHJlYWRzPTEgbG9va2FoZWFkX3RocmVhZHM9MSBzbGljZWRfdGhyZWFkcz0wIG5yPTAgZGVjaW1hdGU9MSBpbnRlcmxhY2VkPTAgYmx1cmF5X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2ludHJhPTAgYmZyYW1lcz0zIGJfcHlyYW1pZD0yIGJfYWRhcHQ9MSBiX2JpYXM9MCBkaXJlY3Q9MSB3ZWlnaHRiPTEgb3Blbl9nb3A9MCB3ZWlnaHRwPTIga2V5aW50PTI1MCBrZXlpbnRfbWluPTEwIHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYWhlYWQ9NDAgcmM9Y3JmIG1idHJlZT0xIGNyZj0yMy4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OSBxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS4wMACAAAAD/2WIhAF/1FdsR9d3o0qTS8/jvcjk5Zq9VAm+CFAGBpvzjvjVksOHKvB/qisMH4VLVgk74yQ2C2hog5+/OYVueD6Mg655MuLECL2n486cPhCIl9KDAVNJV70SSGRXZxVLplO3tWvjApkhFHVt0RHO6lmTF4ju2TE8DIQZtcX1PT9kzO7FKWBJVhzCu1qt4IQDcfPuv1L3OPNuTKkKo4BYhG4Y/ydKSTMikoHvuPZnaCbZJG9lVSvBD/c3VOg3nDVR7Zvq1VzW8qhUsX/g/wJGp2xIRqbbPLU5liMUU5mebsLX4fGUNEkyN/mz+svyqleggiMDIZMKuDKfek4enZDIAf7aBALLk1Pgcp7BRmgvexZasQFZ2VAUqdF/wJF67o4vxGXGENzQs1V+CWUrkOw+FU/ra/vThUV7wIfCRHPT6yXKEHMZF1ELUxX3DGkA1KFJ9SyLLBXY/Nd3zI5zFrJDZ858SiQBET4ppU4iTT695V5SlPzDTUxThQQr19gwBBUQXZJuEePMSqPKEW1Sjnr9+8FLShEhz2ZLTIVHzU++eKKdodxu9K6MYtsf6AYdG+/FGPvMnpTiMtmp4p0MS1SL5wagNNRbbTtVmiEhrQ1Z/x03lzjhJjpCdQbDMyRZccGO7Y14bE6xPkYq8MfZD3uizw8kKio/KPQeCvKaDa9bzFsEG8EhhVBCiQcyy7x8EancsjCC5H258u3+6nf855vNE6yVeHBgI7Qrlny4THXsLMD5Y+kwOOsAYTrdLOf5N0U0g17JkerkYNHRtsHnIr9k0ECJDelzs6BbKH+0cTWujqJG1BrVIJoZZN9tBB2Asc7qNtCulZIBQg5uV5F13hFRMpgKYlYAOsjf157HqAjxlfw6eLiDDwl2j8YsR8FRvrvBXjwQNdwBlFM48JwQVCH/LNDSibiXXZWNXB5mUgvqPglQNYiYZN/EusCK38ceqL5gVIKhlWUDF1f7J2G6f5pb6uwENw6u8zwwpQZ/aJV9NwdED3PDJH4cB1fakvVxP3evKQW/iL7Xndkdp78bjhLTUP+J9rHmnnSh44zppMWpANMf/Cso8kFUk97LNgbsubVtuADDdlZva5BX0uJKnD9CS+uNzHmXZk+vWjYPj3idLuoCdy/qo7wVLnASxuwuXG1y2HVi3+Z9+QLdN8KxTKL6ozgw2/8ehhiyN3xDdyTmzbpeLAb4b4hc1CdxcGNNB6sIZrOEl7KOx8Bht6Vg5J7D1OfmA5vEaqb9zO2e3nWj1WN6ww6kSeAWoG3yxfVnIAl93hy/UG3ZIsU3Offjl+mcLLbWJNDCh083JDz4iqlqctFXlCONViZ6rsr6a6WwbeyraB2M0q/bo19+52dEulJ5ZwAAAD9BmiRsTf/GGuv8Hb+D0oFfFndN1wFYV17fsb3BF2dyiz4BzP4q0buGKuFa36Wwii6gmprP3MWh/LNl9OqQzhYAAAAeQZ5CeIz/+rIYedEHQ8eYeEJjfat51RdJrPQbG4ZxAAAAJwGeYXRCH/Z/hK9uNult8CNxNt1oX5ApBYY1Rc6+SnHUdbNP11lzuAAAAAgBnmNqQh9rQQAAAE9BmmhJqEFomUwIz/GfrutRK3STy48inKi4al4CWP24UUlSAZlo6V/BCNWVQqbflEDWj+1X4qeTFl6rOLDLcNHTTAo4Pb/Tav8LRJ+QLnGBAAAAGkGehkURLGfrHlycPL/Zz16WomJktWgOXCGxAAAACwGepXRCH/Z9BDNhAAAADQGep2pCH+AZtFHzp4AAAAA3QZqpSahBbJlMCEP/RQO+ZkAwM56jX/DA4Zb/TivtpdHcqknf2qXIZze598c7NgG0b1ItePyogA==';
  const result = {test:'WebCodecs VideoFrame',version:1,control:null,samples:[]};
  const describeError = e => ({name:e.name,message:e.message});
  const inspect = async video => {
    const source = {width:video.videoWidth,height:video.videoHeight,currentTime:video.currentTime,paused:video.paused,readyState:video.readyState,mediaKeysAttached:Boolean(video.mediaKeys)};
    let frame;
    try {
      frame = new VideoFrame(video);
      const options = {format:'RGBA'};
      const bytes = new Uint8Array(frame.allocationSize(options));
      const layout = await frame.copyTo(bytes,options);
      const width = frame.visibleRect.width, height = frame.visibleRect.height;
      let nonDark=0, opaque=0, count=0, rgbMaximum=0;
      for(let y=0;y<height;y++) for(let x=0;x<width;x++) {
        const i=layout[0].offset+y*layout[0].stride+x*4;
        const peak=Math.max(bytes[i],bytes[i+1],bytes[i+2]);
        rgbMaximum=Math.max(rgbMaximum,peak);
        if(peak>16) nonDark++;
        if(bytes[i+3]>0) opaque++;
        count++;
      }
      return {source,frameCreated:true,pixelsReadable:true,format:frame.format,width,height,rgbMaximum,nonDarkFraction:nonDark/count,nonTransparentFraction:opaque/count};
    } catch(e) {
      return {source,frameCreated:Boolean(frame),error:describeError(e)};
    } finally {frame?.close();}
  };
  let control, controlURL, target, resumeWasPaused=false;
  try {
    if(typeof VideoFrame!=='function') throw new Error('VideoFrame API unavailable');
    const originals=[...document.querySelectorAll('video')].filter(v=>v.videoWidth>0 && v.videoHeight>0);
    if(originals.length!==1) throw new Error(`Expected one source video, found ${originals.length}`);
    target=originals[0]; resumeWasPaused=target.paused;
    await target.play();
    control=document.createElement('video'); control.muted=true; control.preload='auto';
    const binary=atob(controlMP4);
    controlURL=URL.createObjectURL(new Blob([Uint8Array.from(binary,c=>c.charCodeAt(0))],{type:'video/mp4'}));
    try {
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{cleanup();reject(new Error('Control video load timeout'));},8000);
        const cleanup=()=>{clearTimeout(timer);control.removeEventListener('loadeddata',loaded);control.removeEventListener('error',failed);};
        const loaded=()=>{cleanup();resolve();};
        const failed=()=>{const code=control.error?.code;cleanup();reject(new Error(`Control video load failed (${code})`));};
        control.addEventListener('loadeddata',loaded,{once:true});
        control.addEventListener('error',failed,{once:true});
        control.src=controlURL;
      });
      result.control=await inspect(control);
    } catch(e) {result.control={error:describeError(e)};}
    for(let sample=0;sample<3;sample++) {
      if(sample) await new Promise(resolve=>setTimeout(resolve,400));
      result.samples.push(await inspect(target));
    }
  } catch(e) {result.error=describeError(e);}
  finally {
    if(target && resumeWasPaused) target.pause();
    if(control){control.removeAttribute('src');control.load();}
    if(controlURL) URL.revokeObjectURL(controlURL);
  }
  console.log('WEBCODECS_CONTROL '+JSON.stringify(result.control));
  result.samples.forEach((sample,i)=>console.log('WEBCODECS_NHK_'+i+' '+JSON.stringify(sample)));
  if(result.error) console.log('WEBCODECS_ERROR '+JSON.stringify(result.error));
  return result;
})();

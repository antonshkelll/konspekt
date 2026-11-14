// Konspekt IPC adapter. OAuth/validation/refresh are handled by the unmodified OpenAI DevKit.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { createChatGPT } from './local/index.js';
import { ProxyAgent, setGlobalDispatcher } from 'undici';

// Use the same already-configured HTTPS proxy as the desktop's HTTP client.
if (process.env.KONSPEKT_HTTPS_PROXY) setGlobalDispatcher(new ProxyAgent(process.env.KONSPEKT_HTTPS_PROXY));

const output = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const modeScript = (mode) => `Add-Type -AssemblyName System.Security; $b=[Convert]::FromBase64String([Console]::In.ReadToEnd()); $r=[Security.Cryptography.ProtectedData]::${mode}($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser); [Console]::Out.Write([Convert]::ToBase64String($r))`;
function protect(bytes, mode) {
  const powershell = join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const result = execFileSync(powershell, ['-NoProfile', '-NonInteractive', '-Command', modeScript(mode)], {
    input: Buffer.from(bytes).toString('base64'), encoding: 'utf8', windowsHide: true,
    stdio: ['pipe', 'pipe', 'ignore'], timeout: 15000, maxBuffer: 2 * 1024 * 1024
  });
  return Buffer.from(result.trim(), 'base64');
}
const encryption = {
  id: 'konspekt-dpapi-current-user-v1',
  isAvailable: () => process.platform === 'win32',
  encrypt: (text) => protect(Buffer.from(text, 'utf8'), 'Protect'),
  decrypt: (bytes) => protect(bytes, 'Unprotect').toString('utf8')
};
try {
  const raw = readFileSync(0, 'utf8');
  if (raw.length > 1024 * 1024) throw new Error('input_too_large');
  const request = JSON.parse(raw);
  if (request.op === 'encryption-test') {
    const cipher = encryption.encrypt('konspekt-self-test');
    output({type:'done', result:{ok:encryption.decrypt(cipher)==='konspekt-self-test',encrypted:!cipher.includes(Buffer.from('konspekt-self-test'))}});
  } else {
    const client = createChatGPT({
      appName: 'Konspekt', appId: 'konspekt', redirectPort: 0, sendHostId: true,
      storageDir: join(process.env.APPDATA, 'Konspekt', 'chatgpt'), credentialEncryption: encryption,
      openBrowser: (url) => {
        if (new URL(url).origin !== 'https://auth.openai.com') throw new Error('unexpected_authorization_origin');
        const child = spawn(join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'rundll32.exe'),
          ['url.dll,FileProtocolHandler', url], {detached:true, windowsHide:true, stdio:'ignore'});
        child.unref();
      }
    });
    if (request.op === 'sign-in') {
      const session = await client.signIn();
      const models = session.sharing ? await client.listModels() : [];
      output({type:'done',result:{session,models}});
    } else if (request.op === 'status') {
      output({type:'done',result:{session:await client.getSession()}});
    } else if (request.op === 'models') {
      output({type:'done',result:{models:await client.listModels()}});
    } else if (request.op === 'disconnect') {
      await client.disconnect(); output({type:'done',result:{ok:true}});
    } else if (request.op === 'stream') {
      const session = await client.getSession();
      if (!session.sharing) throw Object.assign(new Error(),{code:'sharing_not_enabled'});
      const result = await client.streamResponse({model:request.model,input:request.input,instructions:request.instructions,
        onDelta:delta=>output({type:'delta',text:delta})});
      if (!result.text.trim()) throw Object.assign(new Error(),{code:'empty_response'});
      output({type:'done',result:{ok:true}});
    } else throw Object.assign(new Error(),{code:'unsupported_operation'});
  }
} catch (error) {
  // Never echo raw SDK/HTTP errors: they can contain submitted content or credentials.
  const code = typeof error?.code==='string'?error.code:'chatgpt_connection_error';
  output({type:'error',code:code+(Number.isInteger(error?.status)?`_http_${error.status}`:'')});
  process.exitCode=1;
}

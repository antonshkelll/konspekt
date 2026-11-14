# ChatGPT authentication runtime

Uses unmodified compiled `packages/local` files from OpenAI Sign in with ChatGPT DevKit,
commit `0a36fefeb913055c8c7a1a29b63d82b96b2e841a`.
Source: https://github.com/openai/sign-in-with-chatgpt-devkit

OpenAI-authored DevKit code is distributed under the included Noncommercial License v1.0.
This personal/local demonstration is noncommercial. Commercial use of that contribution
requires a separate agreement; the application must not be sold with this runtime under
the assumption that this license permits commercial distribution.

`bridge.mjs` is Konspekt's independent stdio adapter. It calls the DevKit without changing it.
OAuth credentials stay in the user's Windows profile and are protected by DPAPI CurrentUser.
The runtime does not read existing browser sessions or another application's credentials.

Node.js 22.14.0 is distributed alongside the binary, with its own license. Node and npm
dependencies are excluded from git; pinned npm dependencies are described by package-lock.json.

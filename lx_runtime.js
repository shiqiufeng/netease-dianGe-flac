'use strict'
/*
 * 洛雪音乐(LX Music)自定义音源 runtime —— 供点歌插件加载你自己的音源脚本。
 *
 * 用法：node lx_runtime.js <音源.js> <payloadJson>
 *   payloadJson = {"source":"kw","quality":"320k","musicInfo":{...}}
 * 输出：一行 JSON {"ok":true,"url":"..."} 或 {"ok":false,"error":"..."}
 *
 * 只实现音源脚本需要的 LX 注入接口：EVENT_NAMES / request / on / send / env /
 * version / utils(buffer, crypto)。搜索由调用方负责（音源脚本只提供 musicUrl）。
 */
const http = require('http')
const https = require('https')
const crypto = require('crypto')
const { URL } = require('url')

const handlers = {}
let initedInfo = null

function lxRequest(url, options, callback) {
  if (typeof options === 'function') { callback = options; options = {} }
  options = options || {}
  let u
  try { u = new URL(url) } catch (e) { return callback(new Error('bad url: ' + url)) }
  const mod = u.protocol === 'https:' ? https : http
  const headers = Object.assign({}, options.headers || {})
  const reqOptions = {
    method: String(options.method || 'GET').toUpperCase(),
    headers,
    timeout: options.timeout || 20000,
  }
  let body = options.body
  if (options.form) {
    body = new URLSearchParams(options.form).toString()
    headers['Content-Type'] = headers['Content-Type'] || 'application/x-www-form-urlencoded'
  } else if (options.formData) {
    body = new URLSearchParams(options.formData).toString()
    headers['Content-Type'] = headers['Content-Type'] || 'application/x-www-form-urlencoded'
  } else if (body && typeof body === 'object') {
    body = JSON.stringify(body)
    headers['Content-Type'] = headers['Content-Type'] || 'application/json'
  }
  if (body) headers['Content-Length'] = Buffer.byteLength(body)

  const req = mod.request(u, reqOptions, (res) => {
    const chunks = []
    res.on('data', (c) => chunks.push(c))
    res.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8')
      let parsed = text
      try { parsed = JSON.parse(text) } catch (e) { /* 保留原文 */ }
      callback(null, { statusCode: res.statusCode, headers: res.headers, body: parsed }, parsed)
    })
  })
  req.on('error', (e) => callback(e))
  req.on('timeout', () => req.destroy(new Error('timeout')))
  if (body) req.write(body)
  req.end()
}

globalThis.lx = {
  EVENT_NAMES: { request: 'request', inited: 'inited', updateAlert: 'updateAlert' },
  env: 'desktop',
  version: '2.11.0',
  request: lxRequest,
  on(event, handler) { handlers[event] = handler },
  send(event, data) { if (event === 'inited') initedInfo = data },
  utils: {
    buffer: {
      from: (...a) => Buffer.from(...a),
      alloc: (...a) => Buffer.alloc(...a),
      concat: (...a) => Buffer.concat(...a),
      toArrayBuffer: (b) => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength),
      buf2str: (b, enc) => Buffer.from(b).toString(enc || 'utf8'),
      str2buf: (s, enc) => Buffer.from(s, enc || 'utf8'),
    },
    crypto: {
      md5: (str) => crypto.createHash('md5').update(str).digest('hex'),
      randomBytes: (size) => crypto.randomBytes(size),
      aesEncrypt: (buffer, mode, key, iv) => {
        const m = String(mode).toLowerCase().replace(/-/g, '')
        const alg = m.includes('ecb') ? 'aes-128-ecb' : 'aes-128-cbc'
        const cipher = crypto.createCipheriv(
          alg, Buffer.from(key), m.includes('ecb') ? null : Buffer.from(iv))
        return Buffer.concat([cipher.update(Buffer.from(buffer)), cipher.final()])
      },
      rsaEncrypt: (buffer, key) => crypto.publicEncrypt(key, Buffer.from(buffer)),
    },
  },
}

const sourceFile = process.argv[2]
const payload = JSON.parse(process.argv[3] || '{}')

function done(obj) {
  process.stdout.write(JSON.stringify(obj))
  process.exit(0)
}

try {
  require(sourceFile)
} catch (e) {
  done({ ok: false, error: '音源加载失败: ' + String((e && e.message) || e) })
}

;(async () => {
  const out = {
    ok: false,
    inited: !!initedInfo,
    sources: initedInfo && initedInfo.sources ? Object.keys(initedInfo.sources) : [],
  }
  try {
    const handler = handlers[globalThis.lx.EVENT_NAMES.request]
    if (!handler) throw new Error('音源没有注册 request handler')
    const res = await handler({
      action: 'musicUrl',
      source: payload.source || 'wy',
      info: { musicInfo: payload.musicInfo || {}, type: payload.quality || '320k' },
    })
    let url = res
    if (res && typeof res === 'object') url = res.url
    out.ok = !!(url && typeof url === 'string' && url.startsWith('http'))
    out.url = typeof url === 'string' ? url : null
  } catch (e) {
    out.error = String((e && e.message) || e).slice(0, 200)
  }
  done(out)
})()

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const loginPage = fs.readFileSync(path.join(root, 'pages/login/index.js'), 'utf8')
const loginMarkup = fs.readFileSync(path.join(root, 'pages/login/index.wxml'), 'utf8')
const guard = fs.readFileSync(path.join(root, 'utils/production-guard.js'), 'utf8')

assert.match(loginPage, /\/auth\/miniprogram\/sms-login/)
assert.doesNotMatch(loginPage, /wechat-login|loginWithWechat|wechatPhoneLogin|setMode/)
assert.doesNotMatch(loginMarkup, />微信登录<|wechat-login|open-type="getPhoneNumber"|data-mode=/)
assert.match(loginMarkup, /验证码登录/)
assert.match(guard, /auth\/miniprogram\/wechat-login/)

console.log('sms-only-miniprogram-policy: ok')

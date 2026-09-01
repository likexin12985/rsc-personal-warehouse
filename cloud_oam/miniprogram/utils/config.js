function getEnvVersion() {
  try {
    return wx.getAccountInfoSync().miniProgram.envVersion || 'release'
  } catch (_) {
    return 'release'
  }
}

function reviewedHttpsUrl(value) {
  const normalized = String(value || '').trim().replace(/\/$/, '')
  if (!/^https:\/\/[^\s\/?#@]+(?::\d+)?(?:\/[^\s?#]*)?$/.test(normalized)) return ''
  return normalized
}

const releaseConfig = require('./release-config')
const envVersion = getEnvVersion()
const API_BASE_URL = envVersion === 'develop'
  ? 'http://127.0.0.1:8000/api'
  : reviewedHttpsUrl(releaseConfig.API_BASE_URL)
const PC_ORIGIN = envVersion === 'develop'
  ? 'http://127.0.0.1:5173'
  : reviewedHttpsUrl(releaseConfig.PC_ORIGIN)

module.exports = { API_BASE_URL, PC_ORIGIN, envVersion, reviewedHttpsUrl }

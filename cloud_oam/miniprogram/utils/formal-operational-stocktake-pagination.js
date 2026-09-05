// The server cursor is the first unseen UUID (inclusive on the next request).
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

function appendTaskPage(previous, page, cursor) {
  const fail = () => { throw new Error('盘点分页标识重复、倒退或游标不连续，请刷新后重新查询') }
  if (!page || !Array.isArray(page.items) || page.items.length > 50) fail()
  const rows = previous.slice()
  let last = rows.length ? rows[rows.length - 1].task_id : null
  if (cursor !== null && (!UUID.test(cursor) || (last && cursor <= last))) fail()
  if (cursor === null && previous.length) fail()
  for (const item of page.items) {
    const id = item.task_id
    if (!UUID.test(id) || (last && id <= last) || (cursor && id < cursor)) fail()
    rows.push(item)
    last = id
  }
  const next = page.next_after_id
  if (next !== null && (!UUID.test(next) || !page.items.length || (last && next <= last) || (cursor && next <= cursor))) fail()
  return { items: rows, next_after_id: next }
}

function accessIdentity(access) {
  if (!access || !access.can_read || !UUID.test(access.person_id || '') || !Number.isSafeInteger(access.authorization_version) || access.authorization_version <= 0) {
    throw new Error('正式盘点身份或权限无效')
  }
  return `${access.person_id}:${access.authorization_version}`
}

module.exports = { appendTaskPage, accessIdentity }

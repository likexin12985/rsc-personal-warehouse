const assert = require('node:assert/strict')
const fs = require('node:fs')
const contract = require('../../utils/stock-return-receiving-contract')
const { history, directory } = JSON.parse(fs.readFileSync(0, 'utf8'))
const expected = { personId: history.person_id, authorizationVersion: history.authorization_version, shipmentId: history.package.shipment_id }
assert.equal(contract.validateHistory(history, expected), history)
assert.equal(contract.validateDirectory(directory, { ...expected, shipmentId: null }), directory)
process.stdout.write('PG16 recipient mini contracts PASS\n')

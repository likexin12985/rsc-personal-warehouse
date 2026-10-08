// Trial scope never grants authority: retained actions still require the
// current server identity, role, scope, version and allowed_actions.
module.exports = Object.freeze({
  scope: 'trial-mvp',
  supplyPlanning: false,
  stocktake: false,
  workOrderMaterial: false,
  stockReturn: false
})

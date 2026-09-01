// Fail-closed release defaults.  The reviewed release process must set both
// HTTPS origins for the target environment before uploading a trial/release
// build; do not point a development build at a production service.
module.exports = {
  API_BASE_URL: '',
  PC_ORIGIN: ''
}

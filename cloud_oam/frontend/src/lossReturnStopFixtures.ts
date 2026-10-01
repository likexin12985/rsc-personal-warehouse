import { identity } from './formalLossReview';
import { requestHash } from './lossCorrectionContracts';
import { stopPending } from './lossReturnStopContracts';
import quantity from './test-fixtures/loss-return-stop/quantity.json';
import serial from './test-fixtures/loss-return-stop/serial.json';
export const fixtures = Object.entries({ quantity, serial }).map(([name, data]) => ({ name, data }));
export async function saved(f: typeof fixtures[number], sealed = false) {
  const command = sealed ? f.data.sealed_command : f.data.original;
  return stopPending({ v: 1, flow: 'return-stop', ...identity(f.data.source), source: f.data.source,
    preview: f.data.preview, command, request_hash: await requestHash(command, 'inverses') });
}

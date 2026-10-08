// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import ScrapSerialCheck from './ScrapSerialCheck';
afterEach(cleanup);
const serials=[{serial_id:'10000000-0000-4000-8000-000000000001',serial_no:'SN-one',qr_code:'QR-one'},{serial_id:'10000000-0000-4000-8000-000000000002',serial_no:'SN-two',qr_code:'QR-two'}];
it('requires SKU, exact case and every distinct physical serial, accepts scanner Enter',()=>{
  const onVerified=vi.fn();render(<ScrapSerialCheck sku="SKU-1" serials={serials} disabled={false} onVerified={onVerified}/>);
  const input=screen.getByRole('textbox',{name:'实物标识'}),sku=screen.getByRole('textbox',{name:'实物物料号'}),button=screen.getByRole('button',{name:'核对这件物资'});
  fireEvent.change(input,{target:{value:'SN-one'}});expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(sku,{target:{value:'SKU-1'}});fireEvent.change(input,{target:{value:'sn-one'}});fireEvent.keyDown(input,{key:'Enter'});expect(screen.getByText(/输入与本次报废的实物标识不匹配/)).toBeTruthy();
  fireEvent.change(input,{target:{value:'SN-one'}});fireEvent.keyDown(input,{key:'Enter'});expect(screen.getByText('已核对 1 / 2 件')).toBeTruthy();expect(onVerified).toHaveBeenLastCalledWith([]);
  fireEvent.change(input,{target:{value:'SN-one'}});fireEvent.click(button);expect(screen.getByText(/已经核对，不能重复计数/)).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox',{name:'标识类型'}),{target:{value:'qr_code'}});fireEvent.change(input,{target:{value:'QR-two'}});fireEvent.click(button);
  expect(screen.getByText('已核对 2 / 2 件')).toBeTruthy();expect(onVerified).toHaveBeenLastCalledWith(serials);
  fireEvent.change(sku,{target:{value:'WRONG-SKU'}});expect(screen.getByText('已核对 0 / 2 件')).toBeTruthy();expect(onVerified).toHaveBeenLastCalledWith([]);
});
it('does not interpret a QR as an SN and refuses ambiguous labels',()=>{
  const onVerified=vi.fn();render(<ScrapSerialCheck sku="SKU-1" serials={[...serials,{...serials[1],serial_id:'10000000-0000-4000-8000-000000000003'}]} disabled={false} onVerified={onVerified}/>);
  fireEvent.change(screen.getByRole('textbox',{name:'实物物料号'}),{target:{value:'SKU-1'}});
  const input=screen.getByRole('textbox',{name:'实物标识'});for(const value of ['QR-one','SN-two']){fireEvent.change(input,{target:{value}});fireEvent.keyDown(input,{key:'Enter'});expect(screen.getByText('已核对 0 / 3 件')).toBeTruthy();}
});

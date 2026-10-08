import { Component, type PropsWithChildren } from 'react';
/** Loading failure must not hide the shell or replay any business command. */
export default class RouteLoadBoundary extends Component<PropsWithChildren,{failed:boolean}> {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){
    if(this.state.failed)return <section className="panel" role="alert"><h1>页面未能加载</h1><p>请重新加载页面。已经提交但结果未确认的操作，请进入对应页面回查原请求。</p><button type="button" onClick={()=>window.location.reload()}>重新加载页面</button></section>;
    return this.props.children;
  }
}

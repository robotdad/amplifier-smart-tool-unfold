/* Fixed typed labels from the generated Babylon runtime. Never HTML/CSS input.
 * One implementation shared by acquisition and presentation in the browser.
 */
(() => {
  "use strict";
  const keys = ["text","x","y","width","height","opacity","color","background","border","shadow"].sort();
  function validate(labels) {
    if (!Array.isArray(labels) || labels.length > 28) throw Error("Invalid label count");
    for (const l of labels) {
      if (!l || Object.keys(l).sort().join() !== keys.join() ||
          typeof l.text !== "string" || [...l.text].length > 40) throw Error("Invalid label fields");
      for (const k of ["x","y","width","height","opacity"])
        if (typeof l[k] !== "number" || !Number.isFinite(l[k]) || Math.abs(l[k]) > 100000 ||
            (["width","height"].includes(k) && l[k] < 0)) throw Error("Invalid label geometry");
      if (l.opacity < 0 || l.opacity > 1) throw Error("Invalid label opacity");
      for (const k of ["color","background","border","shadow"]) {
        const a=l[k];
        if (!Array.isArray(a) || a.length!==4 || a.some(v=>typeof v!=="number"||!Number.isFinite(v)) ||
            a.slice(0,3).some(v=>v<0||v>255) || a[3]<0 || a[3]>1) throw Error("Invalid label color");
      }
    }
    return labels;
  }
  function rgba(text) {
    if (!/^rgba?\([\d., ]+\)$/.test(text) || text.length>80) throw Error("Unsupported label color");
    const a=text.slice(text.indexOf("(")+1,-1).split(",").map(Number);
    if(a.length===3)a.push(1);
    return a;
  }
  function extract() {
    const fixed={fontFamily:"system-ui, sans-serif",fontSize:"22px",fontWeight:"600",lineHeight:"22px",
      letterSpacing:"2.5px",paddingTop:"9px",paddingRight:"16px",paddingBottom:"8px",paddingLeft:"16px",
      borderTopWidth:"1px",borderTopLeftRadius:"999px",textTransform:"uppercase",whiteSpace:"nowrap",zIndex:"1"};
    return validate([...document.querySelectorAll(".label3d")].map(el=>{
      const c=getComputedStyle(el),m=new DOMMatrix(c.transform),r=el.getBoundingClientRect();
      for(const [k,v] of Object.entries(fixed))if(c[k]!==v)throw Error("Unsupported label style "+k);
      if(!/^rgba?\([\d., ]+\) 0px 0px 18px 0px$/.test(c.boxShadow))throw Error("Unsupported label shadow");
      if(m.a!==1||m.b!==0||m.c!==0||m.d!==1)throw Error("Unsupported label transform");
      return {text:el.textContent,x:m.e,y:m.f,width:r.width,height:r.height,opacity:Number(c.opacity),
        color:rgba(c.color),background:rgba(c.backgroundColor),border:rgba(c.borderTopColor),
        shadow:rgba(c.boxShadow.slice(0,c.boxShadow.indexOf(")")+1))};
    }));
  }
  const color=a=>`rgba(${a.join(",")})`;
  function build(labels) {
    return validate(labels).map(l=>{
      const el=document.createElement("div");el.className="label3d";el.textContent=l.text;
      Object.assign(el.style,{position:"absolute",left:"0px",top:"0px",zIndex:"1",pointerEvents:"none",
        whiteSpace:"nowrap",font:"600 22px/1 system-ui,sans-serif",letterSpacing:"2.5px",
        textTransform:"uppercase",padding:"9px 16px 8px",borderRadius:"999px",color:color(l.color),
        backgroundColor:color(l.background),border:"1.5px solid "+color(l.border),
        boxShadow:"0 0 18px "+color(l.shadow),opacity:String(l.opacity),
        transform:`translate(${l.x}px,${l.y}px)`,willChange:"transform,opacity"});
      return el;
    });
  }
  window.__unfoldLayerLabels={validate,extract,build};
})();
const pptxgen = require('pptxgenjs');
const { applyTheme } = require('/home/user/.agents/skills/synced/271f04c0-0c47-4adf-aed1-c48bdf947402_7a310aa7-a822-459c-a835-b2a77ca653fc/pptx/scripts/apply_theme.js');

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'MDEIS Team';
pptx.company = 'MDEIS';
pptx.subject = 'Sample finalist pitch deck for the African Agentic AI Design Challenge';
pptx.title = 'MDEIS — The Long View';
pptx.lang = 'en-US';
pptx.theme = {
  headFontFace: 'Aptos Display',
  bodyFontFace: 'Aptos',
  lang: 'en-US'
};

const C = {
  ink: '14213D',
  navy: '102A43',
  teal: '0B7285',
  mint: 'D9F3EE',
  sky: 'D9EAF7',
  coral: 'F26B5E',
  gold: 'F2C14E',
  cream: 'F7F5EF',
  paper: 'FFFFFF',
  slate: '52606D',
  line: 'D8E1E8',
  green: '2E8B57',
  red: 'C94C4C'
};

const W = 13.333, H = 7.5;
const titleY = 0.48;
const footer = 'MDEIS · Sample finalist pitch · Synthetic data only';

function addText(slide, text, x, y, w, h, opts = {}) {
  slide.addText(text, {
    x, y, w, h, margin: opts.margin ?? 0, isTextBox: true,
    fontFace: opts.fontFace || 'Aptos', fontSize: opts.fontSize || 16,
    color: opts.color || C.ink, bold: opts.bold || false,
    italic: opts.italic || false, breakLine: false,
    align: opts.align || 'left', valign: opts.valign || 'mid',
    fit: 'shrink', paraSpaceAfterPt: opts.paraSpaceAfterPt || 0,
    bullet: opts.bullet, transparency: opts.transparency,
    charSpacing: opts.charSpacing || 0
  });
}
function rect(slide, x, y, w, h, fill, radius = 0.15, line = fill, transparency = 0) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w, h, rectRadius: radius,
    fill: { color: fill, transparency },
    line: { color: line, transparency: line === fill ? 100 : 0, width: 1 }
  });
}
function line(slide, x1, y1, x2, y2, color = C.line, width = 1.5, dash = 'solid') {
  slide.addShape(pptx.ShapeType.line, { x: x1, y: y1, w: x2-x1, h: y2-y1, line: { color, width, dashType: dash, beginArrowType: 'none', endArrowType: 'none' } });
}
function circle(slide, x, y, d, fill, text, textColor = C.paper, fs = 15) {
  slide.addShape(pptx.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill } });
  addText(slide, text, x, y + 0.01, d, d - 0.02, { fontSize: fs, bold: true, color: textColor, align: 'center' });
}
function header(slide, kicker, title, dark = false) {
  addText(slide, kicker.toUpperCase(), 0.62, 0.27, 5.5, 0.25, { fontSize: 10, bold: true, charSpacing: 1.8, color: dark ? C.mint : C.teal });
  addText(slide, title, 0.62, titleY, 12.0, 0.55, { fontFace: 'Aptos Display', fontSize: 30, bold: true, color: dark ? C.paper : C.ink });
}
function footerText(slide, dark = false) {
  addText(slide, footer, 0.62, 7.14, 6.5, 0.16, { fontSize: 8.5, color: dark ? 'B7C7D6' : '7B8794' });
}
function bulletRow(slide, x, y, w, title, body, color = C.teal) {
  circle(slide, x, y + 0.02, 0.28, color, '✓', C.paper, 11);
  addText(slide, title, x + 0.42, y, w - 0.42, 0.22, { fontSize: 14, bold: true, color: C.ink });
  addText(slide, body, x + 0.42, y + 0.25, w - 0.42, 0.42, { fontSize: 11.5, color: C.slate, valign: 'top' });
}
function node(slide, x, y, w, h, n, title, body, fill = C.paper, accent = C.teal) {
  rect(slide, x, y, w, h, fill, 0.12, C.line);
  circle(slide, x + 0.16, y + 0.18, 0.34, accent, String(n), C.paper, 11);
  addText(slide, title, x + 0.62, y + 0.13, w - 0.78, 0.24, { fontSize: 12.5, bold: true, color: C.ink });
  addText(slide, body, x + 0.16, y + 0.57, w - 0.32, h - 0.68, { fontSize: 10.5, color: C.slate, valign: 'top' });
}

// 1. Cover
{
  const s = pptx.addSlide();
  s.background = { color: C.navy };
  addText(s, 'MDEIS', 0.7, 0.55, 2.2, 0.45, { fontSize: 22, bold: true, color: C.gold, charSpacing: 1.6 });
  addText(s, 'The Long View', 0.7, 1.35, 6.7, 1.0, { fontFace: 'Aptos Display', fontSize: 46, bold: true, color: C.paper });
  addText(s, 'From scattered school records to evidence-backed next lessons.', 0.74, 2.55, 5.7, 0.7, { fontSize: 21, color: 'D9EAF7', valign: 'top' });
  addText(s, 'A sample finalist pitch for the African Agentic AI Design Challenge · Education track', 0.74, 3.55, 5.8, 0.42, { fontSize: 12.5, color: 'B7C7D6' });
  rect(s, 7.25, 0.9, 5.25, 5.55, '173B5E', 0.28, '2B5A7B');
  // two-axis motif
  line(s, 7.95, 5.55, 11.88, 5.55, C.gold, 2);
  line(s, 7.95, 5.55, 7.95, 1.65, C.gold, 2);
  addText(s, 'CURRENT BASELINE', 8.25, 5.72, 3.1, 0.22, { fontSize: 10, bold: true, color: C.gold, charSpacing: 1.2 });
  addText(s, 'CHANGE OVER TIME', 7.22, 1.16, 2.2, 0.22, { fontSize: 10, bold: true, color: C.gold, charSpacing: 1.2, align: 'center' });
  const pts = [[8.55,4.55],[9.28,4.15],[9.95,3.75],[10.62,3.1],[11.3,2.5]];
  pts.forEach((p,i)=>{ circle(s,p[0],p[1],0.26,i===pts.length-1?C.coral:C.teal,'',C.paper,1); if(i<pts.length-1) line(s,p[0]+0.25,p[1]+0.13,pts[i+1][0],pts[i+1][1]+0.13,'66B6C4',2); });
  addText(s, 'Longitudinal trend', 8.25, 2.02, 2.0, 0.3, { fontSize: 14, bold: true, color: C.paper });
  addText(s, 'Latitudinal baseline', 10.0, 4.65, 2.0, 0.3, { fontSize: 14, bold: true, color: C.paper });
  addText(s, 'Measure → explain → adapt → re-measure', 0.74, 6.38, 5.7, 0.32, { fontSize: 14, bold: true, color: C.mint });
  footerText(s, true);
  s.addNotes('Opening: MDEIS gives educators the long view of learning. It combines how a learner or class is changing over time with where current evidence sits against the expected baseline, then helps the tutor adapt the next lesson.');
}

// 2. Problem
{
  const s = pptx.addSlide();
  s.background = { color: C.cream };
  header(s, '01 · The problem', 'A term-by-term view hides the learning story');
  addText(s, 'Teachers inherit fragments: marks, attendance, notes and memory. The pattern appears only after the moment when a small adjustment could have mattered.', 0.66, 1.3, 7.0, 0.68, { fontSize: 18, color: C.slate, valign: 'top' });
  // timeline
  const xs = [0.9, 2.4, 3.9, 5.4, 6.9];
  line(s, 1.0, 3.12, 7.2, 3.12, C.teal, 2);
  xs.forEach((x,i)=>{ circle(s,x,2.93,0.38,i===4?C.coral:C.teal,String(i+1),C.paper,12); });
  const labels = ['Quiz','Term','Year','New teacher','Late signal'];
  labels.forEach((t,i)=>addText(s,t,xs[i]-0.2,3.48,0.8,0.25,{fontSize:11,bold:i===4,color:i===4?C.coral:C.slate,align:'center'}));
  rect(s, 8.25, 1.35, 4.35, 4.9, C.paper, 0.2, C.line);
  addText(s, 'The missed opportunity', 8.68, 1.75, 3.35, 0.3, { fontSize: 17, bold: true, color: C.ink });
  addText(s, 'A tutor sees a score.\nMDEIS sees a trajectory.', 8.68, 2.28, 3.3, 0.85, { fontSize: 23, bold: true, color: C.teal, valign: 'top' });
  bulletRow(s, 8.68, 3.55, 3.35, 'Longitudinal view', 'Connects terms and years into a defensible trend.');
  bulletRow(s, 8.68, 4.45, 3.35, 'Latitudinal view', 'Shows the class position against the stage baseline.');
  bulletRow(s, 8.68, 5.35, 3.35, 'Instructional loop', 'Turns evidence into a better next lesson.');
  footerText(s);
  s.addNotes('Problem framing: the issue is not lack of data. It is fragmented data and limited teacher time. The system makes the pattern visible early enough to inform instruction.');
}

// 3. Two lenses
{
  const s = pptx.addSlide();
  s.background = { color: C.paper };
  header(s, '02 · The insight', 'Two measurements make the long view actionable');
  addText(s, 'A snapshot without a trend can mislead. A trend without a baseline lacks context. MDEIS keeps both visible.', 0.66, 1.28, 8.2, 0.42, { fontSize: 17, color: C.slate });
  rect(s, 0.72, 2.05, 5.65, 3.75, C.sky, 0.22, C.sky);
  addText(s, 'LONGITUDINAL', 1.1, 2.42, 2.1, 0.25, { fontSize: 11, bold: true, color: C.teal, charSpacing: 1.7 });
  addText(s, 'What is changing?', 1.1, 2.82, 3.6, 0.55, { fontSize: 27, bold: true, color: C.ink });
  addText(s, 'Same learner or skill across terms and years', 1.1, 3.48, 3.7, 0.32, { fontSize: 14, color: C.slate });
  // mini line
  line(s, 1.16, 5.25, 5.65, 5.25, '8BB8D4', 1.5);
  const p1=[[1.35,4.85],[2.25,4.95],[3.15,4.55],[4.05,4.0],[4.95,3.45]];
  p1.forEach((p,i)=>{ circle(s,p[0],p[1],0.2,C.teal,'',C.paper,1); if(i<p1.length-1) line(s,p[0]+0.2,p[1]+0.1,p1[i+1][0],p1[i+1][1]+0.1,C.teal,2); });
  addText(s, 'trend: improving · steady · declining', 1.12, 5.45, 4.5, 0.2, { fontSize: 10.5, italic: true, color: C.slate });
  rect(s, 6.95, 2.05, 5.65, 3.75, C.mint, 0.22, C.mint);
  addText(s, 'LATITUDINAL', 7.33, 2.42, 2.1, 0.25, { fontSize: 11, bold: true, color: C.green, charSpacing: 1.7 });
  addText(s, 'Where are we now?', 7.33, 2.82, 4.1, 0.55, { fontSize: 27, bold: true, color: C.ink });
  addText(s, 'One learner or the whole class against the stage baseline', 7.33, 3.48, 4.65, 0.32, { fontSize: 14, color: C.slate });
  // baseline band
  addText(s, 'below', 7.37, 4.35, 0.7, 0.2, { fontSize: 10, color: C.red });
  addText(s, 'expected range', 9.03, 4.35, 1.4, 0.2, { fontSize: 10, color: C.green, align: 'center' });
  addText(s, 'above', 11.65, 4.35, 0.7, 0.2, { fontSize: 10, color: C.teal, align: 'right' });
  line(s, 7.45, 4.85, 12.0, 4.85, C.slate, 2);
  rect(s, 8.65, 4.63, 1.75, 0.44, 'A6DCCF', 0.1, 'A6DCCF');
  circle(s, 10.88, 4.70, 0.3, C.coral, '', C.paper, 1);
  addText(s, 'criterion-referenced, not peer-ranked', 7.35, 5.45, 4.8, 0.2, { fontSize: 10.5, italic: true, color: C.slate });
  rect(s, 3.4, 6.15, 6.5, 0.52, C.navy, 0.2, C.navy);
  addText(s, 'Together: measure the trajectory and choose the next teaching move', 3.55, 6.27, 6.2, 0.2, { fontSize: 13, bold: true, color: C.paper, align: 'center' });
  footerText(s);
  s.addNotes('Explain the two lenses. Longitudinal analysis is change over time. Latitudinal analysis is the current position against a standard, including a safe aggregated classroom view. Neither is a ranking.');
}

// 4. Classroom view
{
  const s = pptx.addSlide();
  s.background = { color: C.cream };
  header(s, '03 · Classroom intelligence', 'A tutor sees the class baseline — without seeing a leaderboard');
  addText(s, 'The dashboard surfaces which foundational skills need attention at stage level, then lets the tutor decide how to respond.', 0.66, 1.27, 8.6, 0.42, { fontSize: 17, color: C.slate });
  rect(s, 0.7, 2.0, 7.25, 4.35, C.paper, 0.2, C.line);
  addText(s, 'Form 3 · Literacy baseline · synthetic example', 1.08, 2.36, 4.4, 0.28, { fontSize: 14, bold: true, color: C.ink });
  addText(s, 'Share of observed class evidence', 1.08, 2.75, 3.4, 0.22, { fontSize: 11, color: C.slate });
  // bars
  const bars = [
    ['Below expected range', 42, C.coral],
    ['Within expected range', 46, C.green],
    ['Above expected range', 12, C.teal]
  ];
  bars.forEach((b,i)=>{
    const y=3.3+i*0.8;
    addText(s,b[0],1.08,y,2.35,0.22,{fontSize:11.5,color:C.slate});
    rect(s,3.55,y+0.02,3.0,0.28,'E7EEF3',0.08,'E7EEF3');
    rect(s,3.55,y+0.02,3.0*b[1]/100,0.28,b[2],0.08,b[2]);
    addText(s,`${b[1]}%`,6.72,y,0.55,0.22,{fontSize:12,bold:true,color:b[2],align:'right'});
  });
  addText(s, 'Minimum-group and privacy rules apply before any class view is shown.', 1.08, 5.92, 5.9, 0.22, { fontSize: 10.5, italic: true, color: C.slate });
  rect(s, 8.35, 2.0, 4.25, 4.35, C.navy, 0.2, C.navy);
  addText(s, 'What the tutor can do next', 8.75, 2.4, 3.35, 0.32, { fontSize: 18, bold: true, color: C.paper });
  bulletRow(s, 8.75, 3.05, 3.35, 'Spot the prerequisite', 'See where the stage baseline is not yet met.', C.gold);
  bulletRow(s, 8.75, 4.02, 3.35, 'Adapt the lesson', 'Submit planned notes for evidence-backed refinement.', C.gold);
  bulletRow(s, 8.75, 4.99, 3.35, 'Measure again', 'Use the next assessment to close the loop.', C.gold);
  addText(s, 'No names · No ranks · No labels', 8.75, 5.92, 3.25, 0.22, { fontSize: 12.5, bold: true, color: C.mint });
  footerText(s);
  s.addNotes('This is the tutor-facing cross-sectional view. The percentages are illustrative only. The production version would enforce a minimum group size and suppress identifying detail.');
}

// 5. Lesson adapter
{
  const s = pptx.addSlide();
  s.background = { color: C.paper };
  header(s, '04 · From insight to instruction', 'The tutor submits a lesson — MDEIS helps make it fit the room');
  addText(s, 'The model edits the tutor’s notes, not the tutor’s authority. Every revision comes with a reason and evidence.', 0.66, 1.27, 8.5, 0.42, { fontSize: 17, color: C.slate });
  rect(s, 0.75, 2.0, 5.35, 4.35, 'F4F7F9', 0.22, C.line);
  addText(s, 'Tutor’s planned lesson', 1.12, 2.38, 3.0, 0.3, { fontSize: 16, bold: true, color: C.ink });
  addText(s, 'Objective\nExplain equivalent fractions.\n\nActivity\nMove directly from the definition to independent practice.\n\nAssessment\nFive written questions at the end.', 1.12, 2.95, 4.5, 2.2, { fontSize: 14, color: C.slate, valign: 'top' });
  rect(s, 6.43, 3.35, 0.5, 0.5, C.coral, 0.2, C.coral);
  addText(s, '→', 6.43, 3.38, 0.5, 0.35, { fontSize: 22, bold: true, color: C.paper, align: 'center' });
  rect(s, 7.18, 2.0, 5.4, 4.35, C.mint, 0.22, C.mint);
  addText(s, 'Evidence-backed revision', 7.55, 2.38, 3.5, 0.3, { fontSize: 16, bold: true, color: C.ink });
  addText(s, 'Add a concrete visual example first.\n\nInsert a guided check before independent practice.\n\nUse a two-question exit check to test the prerequisite step.', 7.55, 2.95, 4.45, 1.6, { fontSize: 14, color: C.slate, valign: 'top' });
  rect(s, 7.55, 4.92, 4.45, 0.9, C.paper, 0.12, 'B7DCD2');
  addText(s, 'Why this matters', 7.78, 5.1, 1.45, 0.2, { fontSize: 11, bold: true, color: C.green });
  addText(s, 'Class baseline shows evidence below the expected range for the prerequisite skill.', 7.78, 5.34, 3.85, 0.3, { fontSize: 10.5, color: C.slate, valign: 'top' });
  addText(s, 'Original notes remain visible. Tutor approves the revised plan.', 1.12, 5.93, 4.55, 0.22, { fontSize: 10.5, italic: true, color: C.slate });
  footerText(s);
  s.addNotes('This is the proposed instructional adaptation module. It does not diagnose a learner or prescribe a pathway. It helps the tutor adjust explanation, sequencing, examples and checks based on class-level evidence.');
}

// 6. Agentic workflow
{
  const s = pptx.addSlide();
  s.background = { color: C.cream };
  header(s, '05 · Agentic depth', 'The agent coordinates work; deterministic tools own the numbers');
  addText(s, 'MDEIS is a multi-step system with tool selection, issue handling, evidence checks and approval gates — not a single prompt wrapped around a database.', 0.66, 1.27, 10.5, 0.42, { fontSize: 17, color: C.slate });
  const y=2.25,w=1.55,h=1.35,g=0.16;
  const nodes=[
    ['1','Ingest','Normalize records\nidentify issues',C.teal],
    ['2','Resolve','Ask tutor when\ndata is blocked',C.coral],
    ['3','Measure','Trend + baseline\nfrom approved rules',C.green],
    ['4','Cite','Attach record +\nclause evidence',C.teal],
    ['5','Adapt','Revise lesson\nnotes with reasons',C.gold],
    ['6','Approve','Human reviews\nconsequential output',C.coral]
  ];
  nodes.forEach((n,i)=>{
    const x=0.7+i*(w+g);
    node(s,x,y,w,h,n[0],n[1],n[2],C.paper,n[3]);
    if(i<nodes.length-1) addText(s,'›',x+w+0.015,y+0.46,0.13,0.25,{fontSize:22,bold:true,color:C.slate,align:'center'});
  });
  rect(s, 1.0, 4.45, 11.3, 1.35, C.navy, 0.2, C.navy);
  addText(s, 'If evidence is missing, the agent stops.', 1.42, 4.73, 4.25, 0.35, { fontSize: 20, bold: true, color: C.paper });
  addText(s, 'No silent imputation · No invented scores · No unsupported learner claim', 1.42, 5.2, 8.2, 0.28, { fontSize: 13.5, color: C.mint });
  circle(s, 10.9, 4.78, 0.55, C.gold, '✓', C.navy, 18);
  footerText(s);
  s.addNotes('Judges should see a live task that touches multiple tools. Be explicit about live versus simulated integrations, show the log, and demonstrate the approval step.');
}

// 7. Trust
{
  const s = pptx.addSlide();
  s.background = { color: C.paper };
  header(s, '06 · Defensibility', 'Every consequential statement carries its chain of evidence');
  addText(s, 'The model can phrase an explanation. It cannot manufacture the proof behind it.', 0.66, 1.27, 7.5, 0.42, { fontSize: 17, color: C.slate });
  const layers=[
    ['Evidence', 'Assessment record, teacher note, or class aggregate', C.sky],
    ['Rule', 'Template clause, methodology version, stage expectation', C.mint],
    ['Computation', 'Deterministic tool output, inputs, result hash', 'FFF0D2'],
    ['Explanation', 'Plain-language draft with linked citations', 'FDE0DC'],
    ['Decision', 'Named tutor approves release or lesson revision', C.navy]
  ];
  layers.forEach((l,i)=>{
    const y=2.0+i*0.75;
    rect(s,0.9,y,6.1,0.54,l[2],0.1,l[2]);
    addText(s,l[0],1.18,y+0.12,1.35,0.2,{fontSize:12,bold:true,color:i===4?C.paper:C.ink});
    addText(s,l[1],2.55,y+0.12,4.05,0.2,{fontSize:11.5,color:i===4?'D9EAF7':C.slate});
    if(i<layers.length-1) addText(s,'↓',3.75,y+0.53,0.35,0.22,{fontSize:17,bold:true,color:C.slate,align:'center'});
  });
  rect(s,7.75,2.0,4.75,4.3,C.cream,0.2,C.line);
  addText(s,'What the audit trail answers',8.15,2.4,3.8,0.3,{fontSize:18,bold:true,color:C.ink});
  bulletRow(s,8.15,3.0,3.7,'Why?', 'Which record and clause support this statement?');
  bulletRow(s,8.15,3.95,3.7,'How?', 'Which tool ran, with which inputs and version?');
  bulletRow(s,8.15,4.9,3.7,'Who?', 'Which named human approved the consequence?');
  addText(s,'This is auditability for a parent, tutor, school leader — and a judge.',8.15,5.83,3.75,0.35,{fontSize:11.5,bold:true,color:C.teal,valign:'top'});
  footerText(s);
  s.addNotes('Defensibility is not just a citation in the final sentence. It is the record, rule, computation, explanation and approval chain.');
}

// 8. Synthetic example
{
  const s = pptx.addSlide();
  s.background = { color: C.cream };
  header(s, '07 · What a live run looks like', 'One synthetic example: trend + baseline + next lesson');
  addText(s, 'Illustrative only — not a measured production result.', 9.05, 0.55, 3.2, 0.2, { fontSize: 10, italic: true, color: C.coral, align: 'right' });
  rect(s,0.72,1.55,6.55,4.85,C.paper,0.2,C.line);
  addText(s,'Numeracy evidence across five terms',1.08,1.92,3.5,0.25,{fontSize:15,bold:true,color:C.ink});
  // chart area
  const chartX=1.15, chartY=2.55, chartW=5.35, chartH=2.55;
  [0,5,10,15,20].forEach((v,i)=>{
    const yy=chartY+chartH-(v/20)*chartH;
    line(s,chartX,yy,chartX+chartW,yy,'E6EDF2',0.8);
    addText(s,String(v),chartX-0.38,yy-0.1,0.3,0.2,{fontSize:9,color:C.slate,align:'right'});
  });
  const vals=[8.5,9.5,10.5,12.2,13.4];
  vals.forEach((v,i)=>{
    const x=chartX+0.55+i*1.08; const yy=chartY+chartH-(v/20)*chartH;
    circle(s,x-0.11,yy-0.11,0.22,C.teal,'',C.paper,1);
    if(i<vals.length-1){const nx=chartX+0.55+(i+1)*1.08;const ny=chartY+chartH-(vals[i+1]/20)*chartH;line(s,x+0.11,yy,nx-0.11,ny,C.teal,2);}
    addText(s,['F1 T1','F1 T2','F1 T3','F2 T1','F2 T2'][i],x-0.3,5.2,0.6,0.2,{fontSize:9,color:C.slate,align:'center'});
  });
  // expected band
  rect(s,chartX,chartY+chartH-(14/20)*chartH,chartW,(4/20)*chartH,'D9F3EE',0.02,'D9F3EE',35);
  addText(s,'stage expectation',4.9,3.12,1.2,0.18,{fontSize:9,bold:true,color:C.green,align:'right'});
  addText(s,'Observed statement: scores are improving, while the latest result remains below the current stage range.',1.08,5.78,5.7,0.35,{fontSize:11.5,color:C.slate,valign:'top'});
  rect(s,7.75,1.55,4.85,4.85,C.navy,0.2,C.navy);
  addText(s,'Tutor-facing next move',8.15,1.92,3.5,0.3,{fontSize:16,bold:true,color:C.paper});
  addText(s,'Before independent practice:',8.15,2.52,3.75,0.25,{fontSize:14,bold:true,color:C.gold});
  addText(s,'• Start with a concrete worked example\n• Add a guided check for the prerequisite step\n• Use a short exit check before moving on',8.15,2.95,3.8,1.35,{fontSize:14,color:'D9EAF7',valign:'top'});
  rect(s,8.15,4.75,3.75,0.95,'173B5E',0.12,'2B5A7B');
  addText(s,'Evidence attached',8.4,4.96,1.55,0.2,{fontSize:10.5,bold:true,color:C.mint});
  addText(s,'5 term records · stage clause · revision rationale',8.4,5.25,3.15,0.22,{fontSize:10.5,color:'D9EAF7'});
  footerText(s);
  s.addNotes('Use this as a narrated demo frame. The chart is illustrative. The important pattern is that the system can show improvement without overstating that the current level has met the stage standard.');
}

// 9. Rubric and economics
{
  const s = pptx.addSlide();
  s.background = { color: C.paper };
  header(s, '08 · Built for the Nairobi rubric', 'A credible pilot needs a working system and honest unit economics');
  addText(s, 'The figures below are placeholders for the team’s measured fact sheet — they show what must be instrumented before a pilot.', 0.66, 1.27, 10.7, 0.42, { fontSize: 16, color: C.slate });
  const rubric=[['Tool integration','30%',C.teal],['Problem + local insight','20%',C.coral],['Feasibility + cost','20%',C.gold],['User value + willingness to pay','15%',C.green],['Scale + sustainable impact','15%',C.navy]];
  rubric.forEach((r,i)=>{
    const y=2.0+i*0.65; addText(s,r[0],0.9,y,2.5,0.22,{fontSize:11.5,color:C.slate});
    rect(s,3.55,y+0.02,2.95,0.26,'E7EEF3',0.07,'E7EEF3'); rect(s,3.55,y+0.02,2.95*parseInt(r[1])/30,0.26,r[2],0.07,r[2]);
    addText(s,r[1],6.7,y,0.55,0.22,{fontSize:12,bold:true,color:r[2],align:'right'});
  });
  rect(s,7.85,1.9,4.65,3.8,C.cream,0.2,C.line);
  addText(s,'Pilot fact sheet to bring',8.22,2.28,3.5,0.28,{fontSize:17,bold:true,color:C.ink});
  bulletRow(s,8.22,2.88,3.75,'Token cost per outcome','Measured from logs, not estimated per message.');
  bulletRow(s,8.22,3.78,3.75,'Human time per outcome','Including review, issue resolution and support.');
  bulletRow(s,8.22,4.68,3.75,'Pilot / 10× / 100×','Cost, price and margin at each volume.');
  rect(s,0.9,5.65,11.55,0.72,C.navy,0.16,C.navy);
  addText(s,'North-star pilot metric: tutor time from “new evidence arrives” to “next lesson is ready”.',1.2,5.88,10.95,0.24,{fontSize:16,bold:true,color:C.paper,align:'center'});
  footerText(s);
  s.addNotes('Do not claim these figures are real. Use this slide to show the judges that the project understands their revised criteria and will measure cost per completed outcome, not just token cost per message.');
}

// 10. Close / ask
{
  const s = pptx.addSlide();
  s.background = { color: C.navy };
  addText(s, 'MDEIS', 0.72, 0.6, 2.2, 0.4, { fontSize: 21, bold: true, color: C.gold, charSpacing: 1.5 });
  addText(s, 'The next lesson should know what the last years have taught us.', 0.72, 1.48, 8.8, 1.0, { fontFace: 'Aptos Display', fontSize: 37, bold: true, color: C.paper, valign: 'top' });
  addText(s, 'We are seeking a pilot partner, not permission to automate teacher judgment.', 0.76, 2.82, 7.7, 0.42, { fontSize: 19, color: C.mint });
  rect(s,0.75,4.0,3.55,1.55,'173B5E',0.2,'2B5A7B');
  addText(s,'Pilot ask',1.05,4.28,1.5,0.25,{fontSize:14,bold:true,color:C.gold});
  addText(s,'3 schools · 1 term · synthetic-to-consented transition plan',1.05,4.7,2.95,0.48,{fontSize:15,color:C.paper,valign:'top'});
  rect(s,4.65,4.0,3.55,1.55,'173B5E',0.2,'2B5A7B');
  addText(s,'Partner ask',4.95,4.28,1.7,0.25,{fontSize:14,bold:true,color:C.gold});
  addText(s,'Teacher feedback, secure data pathway, compute and evaluation support',4.95,4.7,2.95,0.48,{fontSize:15,color:C.paper,valign:'top'});
  rect(s,8.55,4.0,3.55,1.55,'173B5E',0.2,'2B5A7B');
  addText(s,'Measure',8.85,4.28,1.5,0.25,{fontSize:14,bold:true,color:C.gold});
  addText(s,'Better evidence → better next lesson → measurable learning change',8.85,4.7,2.95,0.48,{fontSize:15,color:C.paper,valign:'top'});
  addText(s,'Measure · explain · adapt · re-measure',0.75,6.55,5.5,0.32,{fontSize:15,bold:true,color:C.mint});
  footerText(s, true);
  s.addNotes('Close with a concrete pilot request. The product is not asking a partner to surrender educational decisions. It is asking for the chance to make evidence more useful to the people who already make them.');
}

(async () => {
  const out = '/home/user/Documents/MDEIS/MDEIS_Nairobi_Pitch_Deck.pptx';
  await pptx.writeFile({ fileName: out });
  await applyTheme(out, {
    name: 'MDEIS Horizon',
    headFontFace: 'Aptos Display',
    bodyFontFace: 'Aptos',
    colors: { dk1: C.ink, lt1: C.paper, dk2: C.navy, lt2: C.cream, accent1: C.teal, accent2: C.coral, accent3: C.gold, accent4: C.green, accent5: C.sky, accent6: C.mint, hlink: C.teal, folHlink: C.coral }
  });
  console.log(out);
})();


/** Product taxonomy. These are editorial categories, not issuer industry classifications.
 * Matching legacy content uses titles/tags; explicit validated paths take precedence. */
export interface TaxonomyNode { key: string; label: string; match?: RegExp; children?: TaxonomyNode[]; }
const leaf = (key: string, label: string, match: RegExp): TaxonomyNode => ({ key, label, match });
const branch = (key: string, label: string, match: RegExp, children: TaxonomyNode[]): TaxonomyNode => ({ key, label, match, children });
export const CONTENT_TAXONOMY: TaxonomyNode[] = [
  branch('technology', '科技与 AI', /科技|芯片|半导体|算力|人工智能|软件|互联网|数据中心|通信|电子|\b(ai|tech|semiconductor|software|cloud)\b/i, [
    branch('semiconductors', '半导体', /半导体|芯片|晶圆|封装|光刻|\b(chip|semiconductor|foundry)\b/i, [
      leaf('memory', '存储芯片', /存储芯片|存储器|闪存|\b(HBM|DRAM|NAND|DDR\d?|memory chip)\b|海力士|美光/i),
      leaf('compute', '算力芯片', /算力芯片|英伟达|寒武纪|\b(GPU|CPU|ASIC|TPU|NVIDIA|AMD|Intel)\b/i),
      leaf('equipment', '设备与材料', /光刻|刻蚀|薄膜沉积|半导体设备|半导体材料|硅片|光刻胶|\b(ASML|Applied Materials)\b/i),
      leaf('foundry', '制造与封测', /晶圆|代工|封装|封测|台积电|中芯国际|\b(TSMC|foundry|packaging)\b/i),
      leaf('analog', '模拟与功率', /模拟芯片|功率半导体|碳化硅|氮化镓|\b(IGBT|SiC|GaN|PMIC)\b/i),
    ]),
    branch('artificial-intelligence', '人工智能', /人工智能|大模型|智能体|\b(AI|LLM|agent|OpenAI|Anthropic)\b/i, [
      leaf('models', '大模型', /大模型|基础模型|语言模型|DeepSeek|通义|豆包|\b(LLM|GPT|Claude|Gemini|Kimi|OpenAI|Anthropic)\b/i),
      leaf('applications', '应用与智能体', /智能体|AI应用|人工智能应用|智能客服|AI编程|\b(agent|copilot|AI application)\b/i),
      leaf('infrastructure', '算力基础设施', /算力|数据中心|智算中心|服务器|机架|云计算|\b(data center|datacenter|server|hyperscaler)\b/i),
    ]),
    branch('software', '软件与互联网', /软件|互联网|电商平台|网络安全|\b(software|internet|SaaS)\b/i, [
      leaf('enterprise', '企业软件', /企业软件|工业软件|办公软件|\b(SaaS|ERP|CRM|Oracle|Salesforce|SAP)\b/i),
      leaf('platforms', '互联网平台', /互联网平台|腾讯|阿里|美团|字节|拼多多|\b(Meta|Alphabet|Google|Amazon)\b/i),
      leaf('security', '网络安全', /网络安全|信息安全|数据安全|网络攻击|漏洞|\b(cybersecurity|cyberattack)\b/i),
    ]),
    branch('electronics', '通信与电子', /通信|消费电子|手机|光通信|光模块|电路板/i, [
      leaf('optical', '光通信', /光模块|光通信|光纤|光器件|\b(CPO|optical|800G|1\.6T)\b/i),
      leaf('devices', '消费电子', /消费电子|手机|智能穿戴|折叠屏|苹果|\b(iPhone|iPad|Apple|smartphone)\b/i),
      leaf('components', '电子元件', /电路板|覆铜板|电子布|连接器|被动元件|\b(PCB|CCL|MLCC)\b/i),
    ]),
  ]),
  branch('industry', '高端制造', /制造|机械|机器人|工业|自动化|\b(industrial|manufacturing|robot)\b/i, [
    branch('robotics', '机器人', /机器人|\b(robot|robotics|Optimus)\b/i, [
      leaf('humanoid', '人形机器人', /人形机器人|具身智能|宇树|优必选|\b(Optimus|humanoid)\b/i),
      leaf('parts', '核心零部件', /减速器|丝杠|伺服|灵巧手|执行器|关节模组|力矩传感器/i),
      leaf('automation', '工业自动化', /工业机器人|自动化|机器视觉|工控|数控|\b(PLC|CNC)\b/i),
    ]),
    branch('machinery', '机械设备', /机械|设备|重工|机床/i, [
      leaf('construction', '工程机械', /工程机械|挖掘机|起重机|三一重工|徐工|中联重科/i),
      leaf('machine-tools', '机床与工具', /机床|刀具|数控加工|工业母机/i),
      leaf('instruments', '仪器仪表', /仪器|仪表|测量设备|检测设备|科学仪器/i),
    ]),
  ]),
  branch('auto', '汽车产业', /汽车|整车|智驾|\b(auto|automotive|vehicle|Tesla)\b/i, [
    branch('vehicles', '整车', /整车|汽车销量|乘用车|商用车|特斯拉|比亚迪|蔚来|小鹏|理想汽车|\b(Tesla|BYD|Volkswagen)\b/i, [
      leaf('electric', '电动与混动', /电动汽车|新能源汽车|混动|插混|纯电|比亚迪|\b(EV|BEV|PHEV)\b/i),
      leaf('traditional', '传统整车', /燃油车|传统整车|奔驰|宝马|丰田|大众汽车|\b(Toyota|BMW|Mercedes)\b/i),
      leaf('commercial', '商用车', /商用车|重卡|轻卡|客车|卡车|\b(truck|commercial vehicle)\b/i),
    ]),
    branch('components', '零部件与智驾', /汽车零部件|智驾|自动驾驶|车载|汽车电子/i, [
      leaf('driving', '智能驾驶', /智驾|自动驾驶|无人驾驶|激光雷达|\b(ADAS|lidar|robotaxi|FSD)\b/i),
      leaf('electronics', '汽车电子', /汽车电子|车载|智能座舱|域控制器|车规芯片/i),
      leaf('mechanical', '机械零部件', /底盘|制动|轮胎|汽车玻璃|压铸|汽车零部件/i),
    ]),
  ]),
  branch('newenergy', '新能源', /新能源|电池|光伏|风电|储能|氢能|\b(battery|solar|renewable)\b/i, [
    branch('batteries', '电池与储能', /电池|储能|\b(battery|storage system)\b/i, [
      leaf('lithium', '锂电池', /锂电|动力电池|磷酸铁锂|宁德时代|\b(CATL|lithium battery)\b/i),
      leaf('solid-state', '固态与新型电池', /固态电池|钠电|钠离子|锂硫|新型电池/i),
      leaf('storage', '储能系统', /储能|逆变器|\b(BESS|energy storage)\b/i),
    ]),
    branch('generation', '清洁发电', /光伏|风电|氢能|燃料电池|\b(solar|wind power|hydrogen)\b/i, [
      leaf('solar', '光伏', /光伏|硅料|硅片|光伏组件|\b(solar|photovoltaic)\b/i),
      leaf('wind', '风电', /风电|风机|海风|\b(wind power|wind turbine)\b/i),
      leaf('hydrogen', '氢能与燃料电池', /氢能|燃料电池|制氢|\b(hydrogen|SOFC|fuel cell)\b/i),
    ]),
  ]),
  branch('health', '医药健康', /医药|医疗|制药|健康|\b(healthcare|pharma|biotech|drug)\b/i, [
    branch('drugs', '药品研发', /药物|药品|创新药|制药|\b(drug|pharma|biotech)\b/i, [
      leaf('innovative', '创新药', /创新药|抗体|双抗|\b(ADC|CAR-T|GLP-1|PD-1)\b/i),
      leaf('generic', '仿制药与中药', /仿制药|原料药|中药|中成药|集采/i),
      leaf('cxos', '研发生产服务', /医药外包|药明|康龙化成|\b(CRO|CDMO|CMO)\b/i),
    ]),
    branch('medical', '器械与服务', /医疗器械|医院|医疗服务|诊断/i, [
      leaf('devices', '医疗器械', /医疗器械|手术机器人|影像设备|\b(IVD|MRI|CT设备)\b/i),
      leaf('services', '医疗服务', /医疗服务|医院|口腔|眼科|体检/i),
      leaf('distribution', '医药流通', /医药流通|药房|药店|医药商业/i),
    ]),
  ]),
  branch('consumer', '消费零售', /消费|零售|食品|白酒|家电|餐饮|旅游|\b(consumer|retail|food|beverage)\b/i, [
    branch('food', '食品饮料', /食品|饮料|白酒|啤酒|乳品|\b(food|beverage)\b/i, [
      leaf('liquor', '白酒与酒类', /白酒|茅台|五粮液|啤酒|葡萄酒|干邑|烈酒/i),
      leaf('food', '食品与调味品', /食品|调味品|零食|速冻|预制菜/i),
      leaf('drinks', '饮料与乳品', /饮料|乳品|乳业|牛奶|咖啡|茶饮/i),
    ]),
    branch('retail', '零售与品牌', /零售|电商|品牌|美妆|服装/i, [
      leaf('commerce', '电商与商超', /电商|商超|超市|百货|\b(ecommerce|retail)\b/i),
      leaf('beauty', '美妆与服饰', /美妆|化妆品|护肤|服装|纺织|运动品牌/i),
      leaf('appliances', '家电与家居', /家电|空调|冰箱|家具|家居|清洁电器/i),
    ]),
    branch('services', '生活服务', /餐饮|旅游|酒店|文娱/i, [
      leaf('travel', '旅游与酒店', /旅游|酒店|景区|免税|文旅/i),
      leaf('dining', '餐饮', /餐饮|餐厅|连锁餐|外卖/i),
      leaf('entertainment', '传媒与游戏', /传媒|游戏|影视|广告|短剧|\b(gaming|entertainment)\b/i),
    ]),
  ]),
  branch('finance', '金融', /银行|保险|证券|金融|融资|债券|\b(bank|insurance|financial|financing)\b/i, [
    branch('banking', '银行', /银行|\bbank\b/i, [
      leaf('commercial', '商业银行', /商业银行|招商银行|工商银行|建设银行|农业银行|中国银行|\b(commercial bank)\b/i),
      leaf('digital', '数字银行与支付', /数字银行|金融科技|支付|\b(fintech|Monzo|PayPal)\b/i),
      leaf('credit', '信贷与息差', /信贷|贷款|息差|不良率|存款|\b(credit|loan)\b/i),
    ]),
    branch('nonbank', '非银金融', /证券|保险|券商|基金|资管/i, [
      leaf('brokers', '证券与投行', /证券|券商|投行|经纪业务|承销/i),
      leaf('insurance', '保险', /保险|寿险|财险|保费|\binsurance\b/i),
      leaf('asset-management', '基金与资管', /基金|资管|私募|公募|对冲基金|\b(asset management|hedge fund)\b/i),
    ]),
    branch('markets', '资本市场', /资本市场|IPO|并购|融资|债券|数字资产/i, [
      leaf('ipo', 'IPO与再融资', /IPO|上市融资|再融资|增发|配股|招股书/i),
      leaf('bonds', '债券与信用', /债券|信用债|债务|违约|\b(bond|debt)\b/i),
      leaf('digital-assets', '数字资产', /数字资产|比特币|以太坊|稳定币|\b(bitcoin|ethereum|crypto|stablecoin)\b/i),
    ]),
  ]),
  branch('property', '地产建筑', /地产|房地产|建筑|建材|基建|\b(property|real estate|construction)\b/i, [
    branch('property', '房地产', /地产|房地产|住宅|物业/i, [
      leaf('developers', '开发与销售', /地产开发|房地产开发|商品房|新房|房企|拿地/i),
      leaf('services', '物业与租赁', /物业|租赁住房|商业地产|写字楼/i),
      leaf('policy', '地产政策', /限购|房贷|购房政策|地产政策|住房政策/i),
    ]),
    branch('construction', '建筑建材', /建筑|建材|基建/i, [
      leaf('infrastructure', '基础设施', /基建|基础设施|水利|轨道交通/i),
      leaf('materials', '建筑材料', /水泥|玻璃|防水材料|建筑材料/i),
      leaf('engineering', '工程建设', /建筑工程|工程承包|建筑设计|建筑装饰/i),
    ]),
  ]),
  branch('energy', '能源', /能源|石油|原油|天然气|煤炭|电力|核电|\b(energy|oil|gas|coal|power)\b/i, [
    branch('oil-gas', '油气', /油气|石油|原油|天然气|\b(oil|gas|petroleum|LNG)\b/i, [
      leaf('crude', '原油', /原油|油价|石油|\b(crude|Brent|WTI|OPEC)\b/i),
      leaf('gas', '天然气与LNG', /天然气|\b(LNG|natural gas)\b/i),
      leaf('services', '炼化与油服', /炼油|炼化|油服|油田服务|勘探开采|\b(refining|oilfield)\b/i),
    ]),
    branch('power', '电力', /电力|电网|核电|水电|火电|\b(power|grid|nuclear)\b/i, [
      leaf('generation', '发电运营', /发电|火电|水电|电价|电力市场/i),
      leaf('grid', '电网与设备', /电网|变压器|特高压|输配电|电力设备/i),
      leaf('nuclear', '核能', /核电|核能|核聚变|小型堆|\b(SMR|nuclear)\b/i),
    ]),
    branch('coal', '煤炭', /煤炭|煤价|焦煤|焦炭|动力煤|\bcoal\b/i, [
      leaf('thermal', '动力煤', /动力煤|电煤|煤电/i),
      leaf('coking', '焦煤与焦炭', /焦煤|焦炭|焦化/i),
    ]),
  ]),
  branch('materials', '资源材料', /有色|金属|黄金|铜价|化工|新材料|\b(metal|mining|gold|copper)\b/i, [
    branch('metals', '金属矿产', /有色|金属|矿产|黄金|铜价|\b(metal|mining|gold|copper)\b/i, [
      leaf('precious', '贵金属', /黄金|白银|铂金|贵金属|\b(gold|silver|platinum)\b/i),
      leaf('base', '工业金属', /铜价|铝价|锌价|铅价|铁矿石|钢铁|\b(copper|aluminium|aluminum|iron ore|steel)\b/i),
      leaf('strategic', '战略资源', /稀土|锂矿|钴|镍|钨|钼|锗|镓|战略资源/i),
    ]),
    branch('chemicals', '化工与材料', /化工|新材料|树脂|纤维|塑料/i, [
      leaf('basic', '基础化工', /基础化工|化肥|农药|氯碱|化纤|聚酯/i),
      leaf('advanced', '先进材料', /新材料|碳纤维|复合材料|轻量化|特种材料/i),
      leaf('electronic', '电子化学品', /电子化学品|电子气体|湿电子|电子特气|高纯试剂/i),
    ]),
  ]),
  branch('agriculture', '农业', /农业|种植|养殖|粮食|\b(agriculture|farming|grain)\b/i, [
    branch('crops', '种植产业', /种植|粮食|种业|玉米|大豆|小麦/i, [
      leaf('seeds', '种业', /种业|种子|转基因|生物育种/i),
      leaf('grains', '粮食作物', /粮食|玉米|大豆|小麦|水稻/i),
      leaf('inputs', '农资与农机', /农资|农机|农业机械|农业化肥/i),
    ]),
    branch('farming', '养殖产业', /养殖|生猪|禽类|水产|饲料/i, [
      leaf('livestock', '畜禽养殖', /生猪|猪价|禽类|肉鸡|蛋鸡|畜禽/i),
      leaf('aquaculture', '水产养殖', /水产|养鱼|虾|海洋养殖/i),
      leaf('feed', '饲料与动保', /饲料|动保|兽药|动物疫苗/i),
    ]),
  ]),
  branch('transport', '交通运输', /航运|港口|航空|物流|快递|铁路|\b(shipping|airline|logistics)\b/i, [
    branch('shipping', '航运港口', /航运|海运|船运|港口|\b(shipping|freight)\b/i, [
      leaf('containers', '集装箱运输', /集装箱|集运|\bcontainer\b/i),
      leaf('bulk', '油运与干散货', /油运|油轮|干散货|散货船/i),
      leaf('ports', '港口运营', /港口|码头|港务/i),
    ]),
    branch('mobility', '航空与物流', /航空|物流|快递|铁路|\b(airline|logistics)\b/i, [
      leaf('airlines', '航空机场', /航空|机场|航班|\b(airline|airport)\b/i),
      leaf('express', '快递物流', /快递|物流|仓储|货运/i),
      leaf('rail', '铁路公路', /铁路|公路|高铁|高速公路/i),
    ]),
  ]),
  branch('defense', '军工航天', /军工|国防|航天|卫星|\b(defense|aerospace|SpaceX)\b/i, [
    branch('space', '航天产业', /航天|卫星|火箭|\b(SpaceX|satellite|rocket)\b/i, [
      leaf('launch', '火箭与发射', /火箭|发射|运载|\brocket\b/i),
      leaf('satellites', '卫星与低轨', /卫星|星链|低轨|\b(Starlink|satellite)\b/i),
      leaf('applications', '航天应用', /遥感|导航|北斗|卫星互联网/i),
    ]),
    branch('defense', '国防装备', /军工|国防|军用|装备/i, [
      leaf('aviation', '航空装备', /战斗机|军机|航空发动机|无人机/i),
      leaf('naval', '舰船装备', /舰船|军舰|航母|潜艇/i),
      leaf('electronics', '军工电子', /军工电子|军用雷达|军用通信|电子对抗/i),
    ]),
  ]),
  branch('macro', '宏观政策', /宏观|政策|央行|利率|通胀|经济|\b(macro|fed|inflation|rates|GDP)\b/i, [
    branch('monetary', '货币政策', /央行|利率|加息|降息|货币政策|\b(Fed|FOMC|ECB|BOJ)\b/i, [
      leaf('central-banks', '央行决议', /央行|美联储|议息|利率决议|\b(FOMC|ECB|BOJ)\b/i),
      leaf('liquidity', '流动性', /流动性|降准|逆回购|公开市场操作|\b(QE|QT|MLF)\b/i),
      leaf('fx', '汇率', /汇率|人民币|美元指数|日元|欧元|\b(forex|FX|DXY)\b/i),
    ]),
    branch('economy', '经济数据', /经济数据|GDP|CPI|PPI|PMI|非农|就业|通胀/i, [
      leaf('growth', '增长与景气', /GDP|PMI|工业增加值|经济增长|景气指数/i),
      leaf('inflation', '通胀与物价', /通胀|物价|CPI|PPI|PCE|\binflation\b/i),
      leaf('employment', '就业与消费', /就业|非农|失业率|零售数据|消费者信心/i),
    ]),
    branch('fiscal', '财政与产业政策', /财政|税收|专项债|产业政策|补贴/i, [
      leaf('budget', '财政收支', /财政|预算|赤字|专项债|国债发行/i),
      leaf('tax', '税收政策', /税收|增值税|所得税|减税|退税/i),
      leaf('industrial', '产业支持', /产业政策|产业补贴|专项支持|设备更新|以旧换新/i),
    ]),
  ]),
  branch('global', '地缘与贸易', /地缘|政治|战争|冲突|制裁|贸易|关税|\b(geopolitical|war|sanction|tariff|trade)\b/i, [
    branch('trade', '国际贸易', /贸易|关税|进出口|\b(trade|tariff|export|import)\b/i, [
      leaf('tariffs', '关税与谈判', /关税|贸易谈判|贸易协议|\btariff\b/i),
      leaf('controls', '制裁与管制', /制裁|出口管制|投资限制|实体清单|\bsanction\b/i),
      leaf('supply-chain', '全球供应链', /供应链|出海|海外建厂|产业转移/i),
    ]),
    branch('politics', '政治与安全', /政治|地缘|战争|冲突|选举/i, [
      leaf('conflicts', '地区冲突', /战争|冲突|停火|袭击|\b(conflict|war|ceasefire)\b/i),
      leaf('elections', '选举与政局', /选举|大选|中期选举|政局|组阁/i),
      leaf('diplomacy', '外交关系', /外交|峰会|访问|双边关系|多边合作/i),
    ]),
  ]),
];

export function resolveTaxonomyPath(topic: string, subtopic?: string, detail?: string): TaxonomyNode[] {
  const root = CONTENT_TAXONOMY.find(node => node.key === topic);
  if (!root) return [];
  const path = [root];
  const child = root.children?.find(node => node.key === subtopic);
  if (!child) return path;
  path.push(child);
  const grandchild = child.children?.find(node => node.key === detail);
  if (grandchild) path.push(grandchild);
  return path;
}

const cache = new WeakMap<object, Set<string>>();
export function taxonomyMatches(entry: { title: string; tags?: string[]; categoryPaths?: string[][] }): Set<string> {
  const existing = cache.get(entry); if (existing) return existing;
  const found = new Set<string>();
  const candidates = Array.isArray(entry.categoryPaths) ? entry.categoryPaths : [];
  const explicit = candidates.filter(path => Array.isArray(path) && path.every(key => typeof key === 'string') && path.length > 0 && resolveTaxonomyPath(path[0], path[1], path[2]).length === path.length && path.length <= 3);
  if (explicit.length) {
    explicit.forEach(path => path.forEach((_, index) => found.add(path.slice(0, index + 1).join('/'))));
  } else {
    // Feed/source labels describe the container, not the subject of every item.
    const tags = (entry.tags || []).filter(tag => !/^(海外投行(?:报告|研报)?|投行研报|机构研报|研究报告|机构纪要|市场快讯)$/.test(tag.replace(/#/g, '').trim()));
    const text = [entry.title, ...tags].join(' ');
    const visit = (node: TaxonomyNode, parent: string): boolean => {
      const key = parent ? `${parent}/${node.key}` : node.key;
      const childMatches = (node.children || []).map(child => visit(child, key)).some(Boolean);
      const matches = childMatches || Boolean(node.match?.test(text));
      if (matches) found.add(key);
      return matches;
    };
    CONTENT_TAXONOMY.forEach(node => visit(node, ''));
  }
  cache.set(entry, found); return found;
}

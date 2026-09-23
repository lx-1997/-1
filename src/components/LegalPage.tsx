import React from 'react';

type LegalKind = 'privacy' | 'terms';

const updatedAt = '2026-09-03';

const pageStyle: React.CSSProperties = {
  minHeight: '100vh',
  boxSizing: 'border-box',
  padding: '32px 20px 56px',
  background: '#0b0c0f',
  color: '#e5e7eb',
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
  lineHeight: 1.75,
};

const cardStyle: React.CSSProperties = {
  maxWidth: 820,
  margin: '0 auto',
  padding: '28px 30px',
  border: '1px solid #2c2d32',
  borderRadius: 14,
  background: '#17181c',
};

const linkStyle: React.CSSProperties = { color: '#65aefb', textDecoration: 'none' };

const LegalPage: React.FC<{ kind: LegalKind }> = ({ kind }) => {
  const privacy = kind === 'privacy';
  return (
    <main style={pageStyle}>
      <article style={cardStyle}>
        <a href="/" style={linkStyle}>← 返回 Daocaijing</a>
        <h1 style={{ margin: '22px 0 4px', color: '#fff', fontSize: 28 }}>
          {privacy ? 'Daocaijing 隐私政策' : 'Daocaijing 用户协议'}
        </h1>
        <p style={{ color: '#9ca3af', marginTop: 0 }}>生效/更新日期：{updatedAt}</p>

        {privacy ? (
          <>
            <h2>我们处理哪些信息</h2>
            <p>为提供登录、投研工作台、会员、订单和客服功能，我们会处理你主动提交的用户名、邮箱或手机号，以及账号状态和会员信息。使用自选股、提醒、研究和社区功能时，会保存对应的设置和内容。</p>
            <p>登录令牌和部分工作台缓存会保存在当前设备的应用存储中。页面访问、功能使用和错误信息可能以会话标识汇总，用于稳定性、反滥用和产品改进。我们不会把这些信息用于与服务无关的广告画像。</p>

            <h2>信息如何使用和共享</h2>
            <p>信息仅用于身份验证、同步你的设置、发送你主动开启的资讯提醒、处理订单和回复客服。启用 Android 推送时，设备推送令牌会交给 Firebase Cloud Messaging 或实际配置的推送服务，用于投递通知；未启用时不会建立推送订阅。</p>
            <p>我们不会出售个人信息。依法必须披露、保护用户安全或处理基础设施故障时，可能向必要的服务提供商或主管机关提供最低限度的信息。</p>

            <h2>保存、访问和删除</h2>
            <p>我们只在提供服务、处理争议和履行法律义务所需的期限内保存信息。你可以在账户菜单中联系管理员，申请查询、更正或删除账号及相关个人信息；我们会先完成身份核验，再按法律要求处理。</p>

            <h2>安全和变更</h2>
            <p>我们使用 HTTPS、访问控制和日志审计保护服务，并持续修复安全问题。政策发生实质变化时，会在本页面更新日期并在应用内提示。投资数据和 AI 输出仅供研究参考，不构成投资建议。</p>
          </>
        ) : (
          <>
            <h2>服务内容</h2>
            <p>Daocaijing 提供财经资讯、行情展示、研究工具、AI 辅助分析、社区和相关数字内容服务。服务内容、数据源和可用功能可能因地区、账号状态、网络和第三方服务而变化。</p>

            <h2>账号与使用规范</h2>
            <p>你应提供真实、准确且有权使用的注册信息，并妥善保护账号凭据。不得绕过访问控制、批量抓取、干扰服务、侵犯他人权利或将平台内容用于违法用途。发现账号异常时，请立即通过账户菜单联系管理员。</p>

            <h2>内容和投资风险</h2>
            <p>平台内容、行情、研报摘要和 AI 输出可能存在延迟、遗漏或错误，仅用于信息整理和研究参考，不构成证券、基金或其他金融产品的买卖建议，也不保证任何收益。投资决策由你自行作出并承担风险。</p>

            <h2>付费、变更和终止</h2>
            <p>付费内容、会员权益和退款规则以购买页面及订单说明为准。我们会在必要时更新功能、价格或本协议；重大变化会在应用内提示。违反本协议、法律法规或安全要求时，我们可以限制或终止相关账号和服务。</p>

            <h2>联系我们</h2>
            <p>登录后可从账户菜单打开“联系管理员”，提交服务、隐私或账号删除请求。我们会按隐私政策和适用法律处理。</p>
          </>
        )}
      </article>
    </main>
  );
};

export default LegalPage;

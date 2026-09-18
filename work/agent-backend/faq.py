"""FAQ drafting based on intent clusters, never copies external answer promises."""
import json
import sqlite3
from pathlib import Path

TOPICS={
 'create_account':('如何注册账户？','注册渠道、报错信息'),
 'delete_account':('如何注销账户？','账户归属验证、未结订单'),
 'edit_account':('如何修改账户资料？','需要修改的字段、账户归属验证'),
 'recover_password':('忘记密码怎么办？','官方找回入口；不得索取密码或验证码'),
 'registration_problems':('注册失败怎么办？','脱敏报错信息、发生时间'),
 'switch_account':('如何切换账户？','当前登录状态、官方入口'),
 'check_cancellation_fee':('取消订单是否收费？','订单状态、适用取消政策'),
 'contact_customer_service':('如何联系客服？','商家已验证的联系渠道'),
 'contact_human_agent':('可以转人工客服吗？','问题摘要、已尝试步骤'),
 'delivery_options':('有哪些配送方式？','商品、收货地区、商家配送配置'),
 'delivery_period':('预计什么时候送达？','订单号、实时物流与承诺时效'),
 'complaint':('如何提交投诉？','订单号、投诉事项、期望处理方式'),
 'review':('如何评价订单？','订单状态、评价入口'),
 'check_invoice':('如何查询发票状态？','订单号、开票记录'),
 'get_invoice':('如何申请发票？','订单号、依法所需的开票信息'),
 'cancel_order':('如何取消订单？','订单状态、取消条件、明确取消确认'),
 'change_order':('如何修改订单？','订单号、修改内容、履约状态'),
 'place_order':('如何下单？','商品、数量、库存和结算入口'),
 'track_order':('如何查询订单物流？','订单号、身份验证、实时物流记录'),
 'check_payment_methods':('支持哪些支付方式？','当前结算页支持的支付方式'),
 'payment_issue':('支付失败或重复扣款怎么办？','订单号、脱敏支付状态、支付流水核对'),
 'check_refund_policy':('退款需要满足哪些条件？','适用商家政策、订单事实'),
 'get_refund':('我想申请退款，该怎么办？','订单号、身份验证、明确退款意愿'),
 'track_refund':('如何查询退款进度？','退款回执、支付渠道的实时状态'),
 'change_shipping_address':('下单后如何修改收货地址？','订单履约状态、通过安全表单提交的新地址'),
 'set_up_shipping_address':('如何设置收货地址？','官方地址管理入口'),
 'newsletter_subscription':('如何管理订阅通知？','订阅类型、明确订阅或退订意愿')}

def draft(intent):
    question,fields=TOPICS.get(intent,(intent,'业务信息'))
    answer=f'处理此问题前需要核对：{fields}。当前演示尚未接入相应商家系统，不能确认实际办理结果；请通过商家官方渠道核实。'
    evidence=[]
    if intent in {'get_refund','check_refund_policy'}:
        answer='本沙箱需先核对身份、支付与风险状态，并确认客户当前退款意愿。符合规则时退款金额等于实付，自动处理上限为20000最小货币单位；超限或条件不明转人工。该限制仅用于合成演练，不代表其他商家政策。'
        evidence=['refund@1','risk@1']
    elif intent=='track_refund':
        answer='先查询退款回执和订单状态。超时不等于退款失败，不应直接重复退款；本演示没有真实支付渠道到账查询能力，不能承诺到账时间。'
        evidence=['retry@1']
    return question,answer,evidence,fields

def catalog(path):
    path=Path(path)
    if not path.is_file():return dict(available=False,items=[])
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    try:
        items=[]
        for row in con.execute('SELECT * FROM faq_drafts ORDER BY intent'):
            item=dict(row);item['examples']=json.loads(item['examples'])
            q,a,e,f=draft(item['intent'])
            item.update(question_zh=q,answer=a,policy_evidence=e,required_information=f,status='draft',execution_enabled=False)
            items.append(item)
        return dict(available=True,items=items,provider='Bitext Innovations',license='CDLA-Sharing-1.0',license_url='https://cdla.dev/sharing-1-0/',nature='hybrid synthetic',modified=True)
    finally:con.close()

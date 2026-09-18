# 用户提供的模拟运营数据

`user-20260917.txt` 是用户附件的原始副本（无表头），不补造前10条记录。
第10～14列由用户确认：auto_closed、human_takeover、handling_seconds（秒整数）、receipt_verified、error_flag。
第1～9和15列按内容映射为 ticket_id、scenario、customer_id、order_id、sku、amount、created_at、status、resolution、error_type；这部分是解释性映射。时间时区和金额币种未确认。

只进入独立 operations 数据库，不进入 orders/tasks，不用于授权，不作为FAQ知识或模型评测真值，不调用模型或支付接口。

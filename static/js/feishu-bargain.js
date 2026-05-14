// ================================
// 飞书聊一聊（议价）配置管理
// ================================

// 加载飞书聊一聊配置
async function loadFeishuBargainConfig() {
    console.log('加载飞书聊一聊配置');
    
    try {
        // 加载配置
        const response = await fetch(`${apiBase}/api/feishu/bargain-config`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            if (result.success && result.config) {
                const config = result.config;
                
                // 填充表单
                const enabledCheckbox = document.getElementById('feishuBargainEnabled');
                const appIdInput = document.getElementById('feishuAppId');
                const appSecretInput = document.getElementById('feishuAppSecret');
                const encryptKeyInput = document.getElementById('feishuEncryptKey');
                const bargainAccountSelect = document.getElementById('feishuBargainAccount');
                const defaultTextInput = document.getElementById('feishuDefaultBargainText');
                const webhookUrlInput = document.getElementById('feishuWebhookUrl');
                
                if (enabledCheckbox) enabledCheckbox.checked = config.bargain_enabled || false;
                if (appIdInput) appIdInput.value = config.app_id || '';
                if (appSecretInput) appSecretInput.value = config.app_secret || '';
                if (encryptKeyInput) encryptKeyInput.value = config.encrypt_key || '';
                if (defaultTextInput) defaultTextInput.value = config.default_bargain_text || '老板你好！请问这个还在吗？';
                
                // 设置 Webhook URL
                if (webhookUrlInput) {
                    webhookUrlInput.value = `${apiBase}/feishu/webhook`;
                }
                
                // 加载账号列表并设置选中项
                await loadAccountListForBargain(config.bargain_account || '');
            }
        } else {
            console.error('加载飞书聊一聊配置失败');
        }
    } catch (error) {
        console.error('加载飞书聊一聊配置失败:', error);
    }
}

// 加载账号列表用于议价配置
async function loadAccountListForBargain(selectedAccount) {
    try {
        console.log('开始加载账号列表...');
        // 使用 /cookies/details 接口获取完整的账号信息
        const response = await fetch(`${apiBase}/cookies/details`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        console.log('账号列表接口响应:', response.status);
        if (response.ok) {
            const cookies = await response.json();
            console.log('账号列表数据:', cookies);
            const select = document.getElementById('feishuBargainAccount');
            
            if (select && cookies && cookies.length > 0) {
                console.log('找到账号数量:', cookies.length);
                // 保留第一个默认选项
                const defaultOption = select.options[0];
                select.innerHTML = '';
                select.appendChild(defaultOption);
                
                // 添加账号选项
                cookies.forEach(cookie => {
                    console.log('添加账号选项:', cookie.id, cookie.username);
                    const option = document.createElement('option');
                    option.value = cookie.id;
                    option.textContent = `${cookie.id} ${cookie.username ? '(' + cookie.username + ')' : ''}`;
                    if (cookie.id === selectedAccount) {
                        option.selected = true;
                    }
                    select.appendChild(option);
                });
            } else {
                console.log('没有可用的账号或下拉框不存在');
            }
        } else {
            console.error('获取账号列表失败:', response.status);
        }
    } catch (error) {
        console.error('加载账号列表失败:', error);
    }
}

// 保存飞书聊一聊配置
async function saveFeishuBargainConfig() {
    const config = {
        app_id: document.getElementById('feishuAppId')?.value || '',
        app_secret: document.getElementById('feishuAppSecret')?.value || '',
        encrypt_key: document.getElementById('feishuEncryptKey')?.value || '',
        bargain_account: document.getElementById('feishuBargainAccount')?.value || '',
        default_bargain_text: document.getElementById('feishuDefaultBargainText')?.value || '老板你好！请问这个还在吗？',
        bargain_enabled: document.getElementById('feishuBargainEnabled')?.checked || false
    };
    
    try {
        const response = await fetch(`${apiBase}/api/feishu/bargain-config`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        
        const statusDiv = document.getElementById('feishuBargainStatus');
        const statusText = document.getElementById('feishuBargainStatusText');
        
        if (result.success) {
            showToast('飞书聊一聊配置保存成功', 'success');
            if (statusDiv && statusText) {
                statusText.textContent = '配置保存成功！';
                statusDiv.style.display = 'block';
                statusDiv.querySelector('.alert').className = 'alert alert-success mb-0';
            }
        } else {
            showToast('配置保存失败: ' + (result.message || '未知错误'), 'danger');
            if (statusDiv && statusText) {
                statusText.textContent = '保存失败: ' + (result.message || '未知错误');
                statusDiv.style.display = 'block';
                statusDiv.querySelector('.alert').className = 'alert alert-danger mb-0';
            }
        }
    } catch (error) {
        console.error('保存飞书聊一聊配置失败:', error);
        showToast('保存失败: ' + error.message, 'danger');
    }
}

// 测试飞书配置连接
async function testFeishuBargainConfig() {
    showToast('正在测试连接...', 'info');
    
    try {
        const response = await fetch(`${apiBase}/api/feishu/test-connection`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        const result = await response.json();
        
        if (result.success) {
            showToast('连接测试成功！', 'success');
        } else {
            showToast('连接测试失败: ' + (result.message || '请检查配置'), 'warning');
        }
    } catch (error) {
        console.error('测试连接失败:', error);
        showToast('测试连接失败: ' + error.message, 'danger');
    }
}

// 复制 Webhook URL
function copyFeishuWebhookUrl() {
    const webhookUrlInput = document.getElementById('feishuWebhookUrl');
    if (webhookUrlInput) {
        webhookUrlInput.select();
        document.execCommand('copy');
        showToast('Webhook URL 已复制到剪贴板', 'success');
    }
}

// 显示飞书聊一聊使用文档
function showFeishuBargainDoc() {
    const docContent = `
## 飞书聊一聊（议价）功能使用说明

### 功能简介
在飞书群聊中@机器人并发送闲鱼商品链接，系统会自动使用指定闲鱼账号向卖家发送议价消息。

### 配置步骤

#### 1. 创建飞书应用
1. 访问 [飞书开放平台](https://open.feishu.cn/)
2. 点击"创建应用" → "创建企业自建应用"
3. 填写应用名称和描述，点击"创建"

#### 2. 获取应用凭证
1. 进入应用详情页，点击左侧"凭证与基础信息"
2. 复制 **App ID** 和 **App Secret** 到本系统配置中

#### 3. 配置事件订阅
1. 点击左侧"事件与回调" → "事件订阅"
2. 打开"加密策略"，复制 **Encrypt Key** 到本系统配置中（可选）
3. 在"请求地址"中填入本系统的 Webhook URL：
   \`${location.origin}/feishu/webhook\`
4. 点击"保存"

#### 4. 添加机器人到群聊
1. 点击左侧"机器人"，开启机器人功能
2. 将机器人添加到需要使用议价功能的群聊中
3. 确保群成员可以@机器人

#### 5. 配置本系统
1. 填写 App ID、App Secret 和 Encrypt Key
2. 选择用于议价的闲鱼账号
3. 设置默认议价话术
4. 根据需要调整防封保护设置
5. 点击"保存配置"

### 使用方法

在配置了机器人的群聊中发送消息：

\`\`\`
@机器人 https://m.goofish.com/item?id=123456 老板能便宜点吗？
\`\`\`

或只发送链接（使用默认话术）：

\`\`\`
@机器人 https://m.goofish.com/item?id=123456
\`\`\`

### 防封保护机制

系统内置多重防封保护：

1. **随机延迟**：进入聊天框后等待 2-5 秒（可配置）
2. **模拟打字**：根据消息长度模拟打字时间
3. **频率限制**：
   - 连续发送间隔至少 10 秒
   - 每分钟最多 3 条
   - 每小时最多 10 条（可配置）

### 注意事项

1. **账号安全**：建议使用专门的闲鱼账号进行议价，避免主账号被封
2. **频率控制**：新账号建议设置更保守的延迟和频率限制
3. **话术多样化**：避免重复发送相同内容，可准备多套话术轮换使用
4. **代理配置**：建议为议价账号配置独立代理，避免 IP 关联

### 故障排查

**问题：机器人不响应**
- 检查飞书应用是否已发布
- 检查 Webhook URL 是否正确配置
- 检查本系统是否已启用该功能

**问题：消息发送失败**
- 检查议价账号的 Cookie 是否有效
- 检查账号是否被风控或限制
- 查看系统日志获取详细错误信息

**问题：触发频率限制**
- 降低每小时发送限制
- 增加延迟时间
- 分散发送时间，避免集中发送
    `;
    
    // 创建模态框显示文档
    const modalHtml = `
        <div class="modal fade" id="feishuBargainDocModal" tabindex="-1">
            <div class="modal-dialog modal-lg modal-dialog-scrollable">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">
                            <i class="bi bi-book me-2"></i>飞书聊一聊使用文档
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="markdown-body" style="font-size: 16px; line-height: 1.8;">
                            ${docContent.replace(/\n/g, '<br>').replace(/\`\`\`/g, '<pre>').replace(/\`/g, '<code>')}
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // 移除已存在的模态框
    const existingModal = document.getElementById('feishuBargainDocModal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // 添加新模态框
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('feishuBargainDocModal'));
    modal.show();
}

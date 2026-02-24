// BrainstormAI Frontend Application

class BrainstormApp {
    constructor() {
        this.fallbackModels = ['gpt-5-2025-08-07', 'doubao-1.8', 'qwen3-max-2026-01-23', 'gemini-3-pro-preview', 'deepseek-v3.2'];
        this.ws = null;
        this.sessionId = null;
        this.agents = [];
        this.currentMessages = [];
        this.typingMessages = new Map(); // message_id -> partial content
        this.isGenerationPaused = false;
        this.blockedMessageIds = new Set();

        this.init();
    }

    init() {
        // Setup panel elements
        this.setupPanel = document.getElementById('setup-panel');
        this.chatPanel = document.getElementById('chat-panel');
        this.sessionForm = document.getElementById('session-form');
        this.submitBtn = this.sessionForm.querySelector('button[type="submit"]');

        // Chat panel elements
        this.messagesContainer = document.getElementById('messages');
        this.userInput = document.getElementById('user-input');
        this.sendBtn = document.getElementById('send-btn');
        this.stopBtn = document.getElementById('stop-btn');
        this.endBtn = document.getElementById('end-btn');
        this.exportBtn = document.getElementById('export-btn');
        this.statusDiv = document.getElementById('status');
        this.currentTopicSpan = document.getElementById('current-topic');
        this.agentsList = document.getElementById('agents-list');

        // Agent count slider
        this.agentCountSlider = document.getElementById('agent-count');
        this.agentCountValue = document.getElementById('agent-count-value');
        this.agentConfigsContainer = document.getElementById('agent-configs-container');
        this.availableModels = [];
        this.defaultModel = '';

        this.bindEvents();
        this.loadAvailableModels();
    }

    bindEvents() {
        // Slider
        this.agentCountSlider.addEventListener('input', (e) => {
            const count = parseInt(e.target.value);
            this.agentCountValue.textContent = count;
            this.updateAgentConfigs(count);
        });

        // Session form
        this.sessionForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.createSession();
        });

        // User input
        this.userInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        this.sendBtn.addEventListener('click', () => this.sendMessage());
        this.stopBtn.addEventListener('click', () => this.stopGeneration());
        this.endBtn.addEventListener('click', () => this.endSession());
        this.exportBtn.addEventListener('click', () => this.exportSession());
    }

    async createSession() {
        const topic = document.getElementById('topic').value.trim();
        const agentCount = parseInt(this.agentCountSlider.value);

        if (!topic) {
            this.showStatus('请输入讨论主题', 'error');
            return;
        }

        const agentConfigs = [];
        if (this.agentConfigsContainer) {
            const selects = this.agentConfigsContainer.querySelectorAll('.agent-model-select');
            selects.forEach(select => {
                agentConfigs.push({ model_name: select.value });
            });
        }
        
        // Fallback if no cards (shouldn't happen)
        if (agentConfigs.length === 0) {
            for (let i = 0; i < agentCount; i++) {
                agentConfigs.push({ model_name: this.defaultModel });
            }
        }

        this.showStatus('正在创建会话...', 'info');
        this.disableForm();
        
        const originalBtnText = this.submitBtn.innerHTML;
        this.submitBtn.innerHTML = '⏳ 生成中...';

        try {
            const payload = {
                topic: topic,
                agent_count: agentCount,
                agent_configs: agentConfigs
            };

            const response = await fetch('/api/sessions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || '创建会话失败');
            }

            const data = await response.json();
            this.sessionId = data.session_id;
            this.agents = data.agents;

            this.showStatus('会话创建成功，正在连接...', 'success');
            this.switchToChatPanel(data.topic);
            this.connectWebSocket();
            // Restore button state (though panel is hidden)
            this.submitBtn.innerHTML = originalBtnText;
            this.enableForm(); // Re-enable for next time
        } catch (error) {
            console.error('Create session error:', error);
            this.showStatus(`创建会话失败: ${error.message}`, 'error');
            this.submitBtn.innerHTML = originalBtnText;
            this.enableForm();
        }
    }

    switchToChatPanel(topic) {
        this.setupPanel.style.display = 'none';
        this.chatPanel.style.display = 'flex';
        this.currentTopicSpan.textContent = topic;
        this.userInput.focus();
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/sessions/${this.sessionId}`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.showStatus('已连接，等待 AI 就绪...', 'info');
        };

        this.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            this.handleWebSocketMessage(message);
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.showStatus('连接错误，请刷新页面重试', 'error');
        };

        this.ws.onclose = () => {
            console.log('WebSocket closed');
            this.showStatus('连接已关闭', 'info');
        };
    }

    handleWebSocketMessage(message) {
        const { type, data } = message;

        switch (type) {
            case 'agents_ready':
                this.displayAgents(data.agents);
                this.showStatus('AI 已就绪，开始讨论吧！', 'success');
                setTimeout(() => this.clearStatus(), 2000);
                break;

            case 'message_started':
                this.handleMessageStarted(data);
                break;

            case 'message_delta':
                this.handleMessageDelta(data);
                break;

            case 'message_completed':
                this.handleMessageCompleted(data);
                break;

            case 'message_cancelled':
                this.handleMessageCancelled(data);
                break;

            case 'status':
                this.handleStatusEvent(data);
                break;

            case 'error':
                this.showStatus(`错误: ${data.error}`, 'error');
                break;

            case 'session_ended':
                this.showStatus('会话已结束', 'info');
                this.disableInput();
                break;

            default:
                console.log('Unknown message type:', type, data);
        }
    }

    displayAgents(agents) {
        this.agents = agents;
        this.agentsList.innerHTML = agents.map(agent => `
            <div class="agent-card">
                <div class="nickname">${this.escapeHtml(agent.nickname)}</div>
                <div class="persona">${this.escapeHtml(agent.persona)}</div>
                <div class="style">${this.escapeHtml(agent.style)}</div>
            </div>
        `).join('');
    }

    async loadAvailableModels() {
        try {
            const response = await fetch('/api/models');
            if (!response.ok) {
                throw new Error('failed to fetch models');
            }
            const data = await response.json();
            this.availableModels = Array.isArray(data.models) && data.models.length > 0 ? data.models : this.fallbackModels;
            this.defaultModel = data.default_model || this.availableModels[0];
        } catch (error) {
            console.error('Load models error:', error);
            this.availableModels = this.fallbackModels;
            this.defaultModel = this.fallbackModels[0];
        }
        
        // Initial render with default count (3)
        const initialCount = parseInt(this.agentCountSlider.value) || 3;
        this.updateAgentConfigs(initialCount);
    }

    updateAgentConfigs(count) {
        if (!this.agentConfigsContainer) return;

        const currentCards = this.agentConfigsContainer.querySelectorAll('.agent-config-card');
        const currentCount = currentCards.length;

        if (count > currentCount) {
            // Add new cards
            for (let i = currentCount; i < count; i++) {
                const card = this.createAgentConfigCard(i + 1);
                this.agentConfigsContainer.appendChild(card);
                // Trigger reflow for animation
                setTimeout(() => card.classList.add('visible'), 50);
            }
        } else if (count < currentCount) {
            // Remove extra cards
            for (let i = currentCount - 1; i >= count; i--) {
                const card = currentCards[i];
                card.classList.remove('visible');
                setTimeout(() => card.remove(), 300); // Wait for animation
            }
        }
    }

    createAgentConfigCard(index) {
        const card = document.createElement('div');
        card.className = 'agent-config-card';
        
        const avatarColor = this.getRandomGradient();
        
        card.innerHTML = `
            <div class="agent-config-header" style="background: ${avatarColor}">
                <div class="agent-icon">AI ${index}</div>
            </div>
            <div class="agent-config-body">
                <label>选择模型</label>
                <select class="agent-model-select">
                    ${this.availableModels.map(model => 
                        `<option value="${this.escapeHtml(model)}" ${model === this.defaultModel ? 'selected' : ''}>
                            ${this.escapeHtml(model)}
                        </option>`
                    ).join('')}
                </select>
            </div>
        `;
        
        return card;
    }

    getRandomGradient() {
        const colors = [
            ['#ff9a9e', '#fecfef'],
            ['#a18cd1', '#fbc2eb'],
            ['#84fab0', '#8fd3f4'],
            ['#e0c3fc', '#8ec5fc'],
            ['#fccb90', '#d57eeb'],
            ['#e6dee9', '#afc7f8']
        ];
        const randomPair = colors[Math.floor(Math.random() * colors.length)];
        return `linear-gradient(120deg, ${randomPair[0]} 0%, ${randomPair[1]} 100%)`;
    }

    handleMessageStarted(data) {
        const { message_id, nickname, action } = data;

        if (this.isGenerationPaused) {
            this.blockedMessageIds.add(message_id);
            return;
        }
        
        // Create a typing indicator
        this.typingMessages.set(message_id, '');
        
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message message-ai message-typing';
        msgDiv.id = `msg-${message_id}`;
        msgDiv.innerHTML = `
            <div class="message-header">
                <div class="message-avatar">${this.getAgentInitial(nickname)}</div>
                <div class="message-nickname">${this.escapeHtml(nickname)}</div>
            </div>
            <div class="message-content" id="content-${message_id}">正在思考</div>
        `;
        
        this.messagesContainer.appendChild(msgDiv);
        this.scrollToBottom();
    }

    handleMessageDelta(data) {
        const { message_id, token } = data;

        if (this.blockedMessageIds.has(message_id)) {
            return;
        }
        
        if (!this.typingMessages.has(message_id)) {
            this.typingMessages.set(message_id, '');
        }
        
        const currentContent = this.typingMessages.get(message_id);
        const newContent = currentContent + token;
        this.typingMessages.set(message_id, newContent);
        
        const contentDiv = document.getElementById(`content-${message_id}`);
        if (contentDiv) {
            contentDiv.textContent = newContent;
            this.scrollToBottom();
        }
    }

    handleMessageCompleted(data) {
        const { message_id, nickname, content, author_type, author_name } = data;
        if (this.blockedMessageIds.has(message_id)) {
            this.typingMessages.delete(message_id);
            this.blockedMessageIds.delete(message_id);
            return;
        }
        const resolvedAuthorType = author_type || (nickname ? 'ai' : 'user');
        
        this.typingMessages.delete(message_id);
        
        let msgDiv = document.getElementById(`msg-${message_id}`);
        
        if (!msgDiv) {
            // Message wasn't started (user message)
            msgDiv = document.createElement('div');
            msgDiv.id = `msg-${message_id}`;
            this.messagesContainer.appendChild(msgDiv);
        }
        
        // Remove typing class and update content
        msgDiv.className = `message message-${resolvedAuthorType}`;
        
        const speakerName = resolvedAuthorType === 'ai' ? (nickname || 'AI') : (author_name || '用户');
        msgDiv.innerHTML = `
            <div class="message-header">
                <div class="message-avatar">${this.getSpeakerInitial(speakerName)}</div>
                <div class="message-nickname">${this.escapeHtml(speakerName)}</div>
            </div>
            <div class="message-content">${this.escapeHtml(content)}</div>
        `;
        
        this.scrollToBottom();
    }

    handleMessageCancelled(data) {
        const { message_id, nickname, content } = data;
        this.blockedMessageIds.delete(message_id);
        this.typingMessages.delete(message_id);
        const msgDiv = document.getElementById(`msg-${message_id}`);
        if (!msgDiv) return;

        const finalContent = (content || '').trim();
        if (!finalContent) {
            if (msgDiv.classList.contains('message-typing')) {
                msgDiv.remove();
            }
            return;
        }

        msgDiv.className = 'message message-ai';
        msgDiv.innerHTML = `
            <div class="message-header">
                <div class="message-avatar">${this.getSpeakerInitial(nickname || 'AI')}</div>
                <div class="message-nickname">${this.escapeHtml(nickname || 'AI')}</div>
            </div>
            <div class="message-content">${this.escapeHtml(finalContent)}</div>
        `;
        this.scrollToBottom();
    }

    handleStatusEvent(data) {
        const { action, nickname, status } = data;

        if (status === 'generation_stopped') {
            this.isGenerationPaused = true;
            return;
        }
        
        if (action === 'silent') {
            // Optionally show that an agent chose to stay silent
            console.log(`${nickname} chose to stay silent`);
        } else {
            console.log(`${nickname} action: ${action}`);
        }
    }

    sendMessage() {
        const content = this.userInput.value.trim();
        if (!content || !this.ws) return;

        this.isGenerationPaused = false;
        this.blockedMessageIds.clear();

        this.ws.send(JSON.stringify({
            type: 'user_message',
            content: content,
        }));

        this.userInput.value = '';
        this.userInput.focus();
    }

    stopGeneration() {
        if (!this.ws) return;

        this.isGenerationPaused = true;
        this.typingMessages.forEach((_, messageId) => this.blockedMessageIds.add(messageId));

        this.ws.send(JSON.stringify({
            type: 'stop',
        }));

        this.showStatus('已停止 AI 生成', 'info');
    }

    async endSession() {
        if (!confirm('确定要结束会话吗？')) return;

        if (this.ws) {
            this.ws.send(JSON.stringify({
                type: 'end_session',
            }));
        }

        try {
            await fetch(`/api/sessions/${this.sessionId}/end`, {
                method: 'POST',
            });
        } catch (error) {
            console.error('End session error:', error);
        }

        this.disableInput();
        this.showStatus('会话已结束', 'success');
    }

    async exportSession() {
        try {
            this.showStatus('正在导出...', 'info');
            
            const response = await fetch(`/api/sessions/${this.sessionId}/export`);
            
            if (!response.ok) {
                throw new Error('导出失败');
            }

            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `brainstorm_${this.sessionId}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);

            this.showStatus('导出成功', 'success');
            setTimeout(() => this.clearStatus(), 2000);
        } catch (error) {
            console.error('Export error:', error);
            this.showStatus('导出失败', 'error');
        }
    }

    getAgentInitial(nickname) {
        return nickname ? nickname.charAt(0) : '?';
    }

    getSpeakerInitial(name) {
        return name ? name.charAt(0) : '?';
    }

    showStatus(message, type) {
        this.statusDiv.textContent = message;
        this.statusDiv.className = `status ${type}`;
        this.statusDiv.style.display = 'block';
    }

    clearStatus() {
        this.statusDiv.style.display = 'none';
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    disableForm() {
        this.sessionForm.querySelectorAll('input, select, button').forEach(el => {
            el.disabled = true;
        });
    }

    enableForm() {
        this.sessionForm.querySelectorAll('input, select, button').forEach(el => {
            el.disabled = false;
        });
    }

    disableInput() {
        this.userInput.disabled = true;
        this.sendBtn.disabled = true;
        this.stopBtn.disabled = true;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new BrainstormApp();
});

<template>
  <div class="admin-app">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">S</div>
        <div>
          <div class="brand-title">思德科技展厅</div>
          <div class="brand-subtitle">Command Center</div>
        </div>
      </div>

      <button
        v-for="tab in tabs"
        :key="tab.key"
        :class="['nav-item', { active: activeTab === tab.key }]"
        @click="activeTab = tab.key"
      >
        <span>{{ tab.label }}</span>
        <small>
          {{ tab.hint }}
        </small>
      </button>

      <div class="connection" :class="{ online: relayConnected }">
        <span class="status-dot"></span>
        {{ relayConnected ? '中控在线' : '中控离线' }}
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div>
          <h1>{{ currentTab?.label }}</h1>
          <p>{{ currentTab?.desc }}</p>
        </div>
        <div class="top-actions">
          <el-button @click="refreshAll">刷新</el-button>
          <el-button type="primary" @click="activeTab = 'specials'">配置专场</el-button>
        </div>
      </header>

      <section v-if="activeTab === 'dashboard'" class="page">
        <div class="stats-grid">
          <article class="stat-card screen-online-card">
            <span>在线展示屏幕</span>
            <strong>{{ onlineScreens.length }}</strong>
            <em>{{ screenSummaryText }}</em>
          </article>
          <article class="stat-card">
            <span>知识库文件</span>
            <strong>{{ mediaFiles.length }}</strong>
            <em>PPT / 视频 / 图片</em>
          </article>
          <article class="stat-card">
            <span>QA问答</span>
            <strong>{{ enabledQaCount }}</strong>
            <em>启用 / 共 {{ qaItems.length }} 条</em>
          </article>
        </div>

        <div class="panel">
          <div class="panel-header">
            <div>
              <h2>最近对话</h2>
              <p>来自中控、管理端和新智慧大脑接口的问答记录。</p>
            </div>
            <div class="panel-actions">
              <el-button text @click="tagHelpVisible = true">标签说明</el-button>
              <el-button text @click="conversations = []">清空</el-button>
            </div>
          </div>
          <el-empty v-if="conversations.length === 0" description="暂无对话记录" />
          <div v-else class="conversation-list">
            <article
              v-for="item in pagedConversations"
              :key="item.id"
              class="conversation-item"
              :class="{ pending: item.pending, typing: item.typingInput || item.typingAnswer }"
            >
              <div class="conversation-meta">
                <span>{{ formatTime(item.time) }} | {{ conversationDeviceLabel(item) }}</span>
                <span v-if="item.progressStage && (item.pending || item.typingAnswer)" class="conversation-progress-stage">
                  {{ answerProgressStageLabel(item.progressStage) }}
                </span>
                <span v-if="item.answerSource" class="conversation-source">{{ item.answerSource }}</span>
                <span v-if="item.finalRoute" class="conversation-route">{{ item.finalRoute }}</span>
              </div>
              <div class="conversation-question">
                {{ item.visibleInput !== undefined ? item.visibleInput : item.input }}
                <i v-if="item.typingInput" class="typing-caret"></i>
              </div>
              <div v-if="item.pending && !item.reply" class="conversation-progress">
                <span class="progress-orbit"></span>
                {{ item.progressMessage || '正在处理…' }}
              </div>
              <div v-else class="conversation-answer">
                {{ item.visibleReply !== undefined ? item.visibleReply : item.reply }}
                <i v-if="item.typingAnswer" class="typing-caret"></i>
              </div>
              <div v-if="item.webSources?.length" class="conversation-sources">
                <b>联网来源</b>
                <a v-for="source in item.webSources" :key="source.url || source.title" :href="source.url" target="_blank" rel="noreferrer">
                  {{ source.evidenceId ? `【${source.evidenceId}】` : '' }}{{ source.title || source.url }}
                </a>
              </div>
              <div v-if="item.knowledgeSources?.length" class="conversation-sources knowledge-sources">
                <b>知识来源</b>
                <span v-for="source in item.knowledgeSources" :key="`${source.fileName}-${source.page || ''}`">
                  {{ source.fileName }}<template v-if="source.page"> · 第{{ source.page }}页</template>
                </span>
              </div>
              <details v-if="item.routeTrace?.length" class="route-trace">
                <summary>查看路由过程</summary>
                <div v-for="(step, index) in item.routeTrace" :key="`${step.handler}-${index}`">
                  {{ step.handler }} · {{ step.status || (step.matched ? 'MATCHED' : 'SKIPPED') }}<span v-if="step.reason"> · {{ step.reason }}</span><span v-if="step.ruleName"> · {{ step.ruleName }}</span>
                </div>
              </details>
            </article>
          </div>
          <div v-if="conversations.length > conversationPageSize" class="table-pagination compact">
            <el-pagination
              v-model:current-page="conversationPage"
              :page-size="conversationPageSize"
              :total="conversations.length"
              layout="prev, pager, next, total"
              background
            />
          </div>
        </div>

      </section>

      <section v-if="activeTab === 'chat'" class="page chat-page">
        <div class="chat-workspace">
          <aside class="chat-session-rail">
            <el-button type="primary" class="new-chat-button" @click="startNewSimulatorConversation">新建会话</el-button>
            <div class="chat-session-list">
              <div
                v-for="session in conversationSessions"
                :key="session.id"
                :class="['chat-session-item', { active: selectedConversationId === session.id }]"
                @click="selectConversationSession(session)"
                @keydown.enter="selectConversationSession(session)"
                role="button"
                tabindex="0"
              >
                <strong>{{ session.title }}</strong>
                <span>{{ formatTime(session.lastAt) }} · {{ session.items.length }} 轮</span>
                <em>{{ session.archived ? '已归档' : '进行中' }}</em>
                <div class="chat-session-actions">
                  <button
                    type="button"
                    class="chat-session-rename"
                    title="重命名会话"
                    @click.stop="renameConversationSession(session)"
                  >重命名</button>
                  <button
                    type="button"
                    class="chat-session-delete"
                    title="删除会话"
                    @click.stop="deleteConversationSession(session)"
                  >删除</button>
                </div>
              </div>
            </div>
          </aside>

          <section class="chat-dialog-panel">
            <header class="chat-dialog-header">
              <div>
                <h2>{{ selectedConversationSession?.title || '新会话' }}</h2>
                <p>静默 3 分钟后自动归档；从左侧选择历史会话后可继续提问。</p>
              </div>
              <span :class="['chat-session-state', { archived: selectedConversationSession?.archived }]">
                {{ selectedConversationSession?.archived ? '历史会话' : '当前会话' }}
              </span>
            </header>
            <div class="chat-message-list">
              <el-empty v-if="!selectedConversationItems.length" description="输入问题开始一次新会话" />
              <template v-else>
                <article v-for="item in selectedConversationItems" :key="item.id" class="chat-turn">
                  <div
                    class="chat-bubble user-bubble"
                    :data-time="`提问时间：${formatConversationTime(item.questionTime || item.time)}`"
                  >
                    <p>{{ item.visibleInput !== undefined ? item.visibleInput : item.input }}</p>
                  </div>
                  <div
                    class="chat-bubble assistant-bubble"
                    :data-time="`回答时间：${formatConversationTime(item.answerTime || item.time)}`"
                  >
                    <small>{{ item.answerSource || '系统' }}</small>
                    <div v-if="item.pending && !item.reply" class="conversation-progress">
                      <span class="progress-orbit"></span>{{ item.progressMessage || '正在处理…' }}
                    </div>
                    <p v-else>{{ item.visibleReply !== undefined ? item.visibleReply : item.reply }}</p>
                  </div>
                </article>
              </template>
            </div>
            <footer class="chat-composer">
              <el-select v-model="simulatorTarget" class="simulator-target">
                <el-option label="全部屏幕" value="all" />
                <el-option v-for="screen in onlineScreens" :key="screen.deviceId || screen.id" :label="`${screen.screenNumber || '-'} 号屏`" :value="screen.screenNumber" />
              </el-select>
              <el-input v-model="simulatorText" type="textarea" :rows="3" placeholder="输入问题或控制指令；Ctrl+Enter 发送" @keyup.ctrl.enter="sendSimulatorQuestion" />
              <el-button type="primary" :loading="simulatorSending" @click="sendSimulatorQuestion">发送</el-button>
            </footer>
          </section>

          <aside class="chat-diagnostic-panel">
            <h3>本轮诊断</h3>
            <template v-if="selectedDiagnosticTurn">
              <dl>
                <dt>回答来源</dt><dd>{{ selectedDiagnosticTurn.answerSource || '-' }}</dd>
                <dt>最终路由</dt><dd>{{ selectedDiagnosticTurn.finalRoute || '-' }}</dd>
                <dt>联网状态</dt><dd>{{ selectedDiagnosticTurn.webSearchStatus || '-' }}</dd>
                <dt>目标屏幕</dt><dd>{{ selectedDiagnosticTurn.target || simulatorTarget }}</dd>
              </dl>
              <details v-if="selectedDiagnosticTurn.routeTrace?.length" open class="route-trace chat-route-trace">
                <summary>路由过程</summary>
                <div v-for="(step, index) in selectedDiagnosticTurn.routeTrace" :key="`${step.handler}-${index}`">
                  {{ step.handler }} · {{ step.status || (step.matched ? 'MATCHED' : 'SKIPPED') }}<span v-if="step.reason"> · {{ step.reason }}</span>
                </div>
              </details>
            </template>
            <el-empty v-else description="暂无诊断信息" />
          </aside>
        </div>
      </section>

      <section v-if="activeTab === 'smartExhibition'" class="page smart-exhibition-page">
        <iframe
          class="smart-exhibition-frame"
          :src="smartExhibitionUrl"
          title="智能布展系统"
        ></iframe>
      </section>

      <section v-if="activeTab === 'avatar'" class="page avatar-page">
        <div class="avatar-stats-grid">
          <article class="stat-card avatar-state-card">
            <span>控制服务</span>
            <strong :class="{ offline: !avatarStatus.online }">{{ avatarStatus.online ? '在线' : '离线' }}</strong>
            <em>{{ avatarStatus.session?.deviceId || '等待数字人客户端连接' }}</em>
          </article>
          <article class="stat-card avatar-state-card">
            <span>数字人状态</span>
            <strong :class="{ offline: !avatarStatus.desiredOpen }">{{ avatarStatus.desiredOpen ? '已打开' : '已退出' }}</strong>
            <em>{{ avatarStatus.speaking ? '正在播报' : '当前空闲' }}</em>
          </article>
          <article class="stat-card avatar-state-card">
            <span>最近指令</span>
            <strong class="command-name">{{ avatarStatus.lastCommand?.channel || '暂无' }}</strong>
            <em>{{ formatAvatarTime(avatarStatus.lastCommand?.at) }}</em>
          </article>
          <article class="stat-card avatar-state-card">
            <span>数字人屏幕</span>
            <strong class="command-name">{{ avatarTarget.label }}</strong>
            <em>{{ avatarTarget.online ? '目标在线' : '目标离线' }}</em>
          </article>
        </div>

        <div class="panel voice-panel">
          <div class="panel-header">
            <div>
              <h2>语音设置</h2>
              <p>选择音色后先试听，保存后从下一次现场播报生效。试听只在当前电脑播放。</p>
            </div>
            <el-button type="primary" :loading="ttsSaving" @click="saveTtsVoice">保存设置</el-button>
          </div>

          <p>当前已保存：{{ savedTtsLabel }} <strong v-if="ttsDirty"> · 有未保存修改</strong></p>
          <p>本地语音服务：{{ localTtsStatus.online ? (localTtsStatus.ready ? (localTtsStatus.busy ? '忙碌' : '就绪') : '准备中') : '离线' }}
            <el-button size="small" @click="refreshTtsCatalog">刷新音色与状态</el-button>
          </p>
          <el-alert v-if="ttsState.provider === 'qwen3'" title="本地音色由展示屏播放，数字人继续显示；当前数字人软件尚不支持本地音频口型同步。" type="info" :closable="false" show-icon />
          <div class="voice-provider-row">
            <button
              v-for="provider in ttsProviderOptions"
              :key="provider.key"
              :class="['voice-provider-button', { active: ttsState.provider === provider.key }]"
              @click="selectTtsProvider(provider.key)"
            >
              <span>{{ provider.name }}</span>
            </button>
          </div>

          <div class="voice-grid">
            <button
              v-for="voice in voiceOptions"
              :key="voice.key"
              :class="['voice-card', { active: ttsState.voice === voice.key }]"
              @click="ttsState.voice = voice.key"
            >
              <span>{{ ttsState.providerName || '播报音色' }}</span>
              <strong>{{ voice.name }}</strong>
              <small>{{ voiceFullName(voice.key) }}</small>
            </button>
          </div>

          <div class="voice-test">
            <el-input v-model="ttsPreviewText" placeholder="输入试听文本（最多 300 字）" :maxlength="300" />
            <el-button :loading="ttsTesting" @click="testTtsVoice">在本机试听</el-button>
            <el-button @click="stopTtsPreview">停止试听</el-button>
          </div>

          <audio ref="ttsAudio" controls class="audio-player"></audio>
        </div>

        <div class="panel avatar-panel">
          <div class="panel-header">
            <div>
              <h2>数字人运行控制</h2>
              <p>打开和退出会控制 MoFa App；显示和隐藏只控制当前画面。</p>
            </div>
            <el-button :loading="avatarStatusLoading" @click="refreshAvatarStatus(false)">刷新状态</el-button>
          </div>
          <div v-if="ttsState.provider !== 'qwen3'" class="avatar-tts-switch" :class="{ enabled: ttsState.avatarEnabled }">
            <div>
              <strong>使用数字人播报</strong>
              <p>{{ ttsState.avatarEnabled ? '已开启：展厅回答不再调用普通 TTS，统一由数字人讲话。' : '已关闭：展厅回答继续使用讯飞普通 TTS。' }}</p>
            </div>
            <el-switch
              v-model="ttsState.avatarEnabled"
              size="large"
              inline-prompt
              active-text="开启"
              inactive-text="关闭"
              :loading="avatarTtsSaving"
              @change="saveAvatarTtsSwitch"
            />
          </div>
          <div class="avatar-target-selector">
            <span>数字人显示屏幕</span>
            <el-radio-group
              v-model="avatarTarget.target"
              :disabled="avatarTargetBusy"
              @change="selectAvatarTarget"
            >
              <el-radio-button
                v-for="item in avatarTarget.targets"
                :key="item.target"
                :label="item.target"
                :disabled="!item.online"
              >{{ item.label }}</el-radio-button>
            </el-radio-group>
          </div>
          <div class="avatar-actions">
            <el-button type="primary" :loading="avatarBusy === 'open'" :disabled="!avatarStatus.online" @click="openAvatar">打开数字人</el-button>
            <el-button type="danger" plain :loading="avatarBusy === 'exit'" :disabled="!avatarStatus.online" @click="exitAvatar">退出数字人</el-button>
            <el-button :loading="avatarBusy === 'show'" :disabled="!avatarStatus.online" @click="showAvatar">显示画面</el-button>
            <el-button :loading="avatarBusy === 'hide'" :disabled="!avatarStatus.online" @click="hideAvatar">隐藏画面</el-button>
            <el-button :loading="avatarBusy === 'reset'" :disabled="!avatarStatus.online" @click="resetAvatar">全屏归位</el-button>
          </div>
          <div v-if="avatarLastError" class="avatar-error">{{ avatarLastError }}</div>
        </div>

        <div class="avatar-control-grid">
          <div class="panel avatar-panel">
            <div class="panel-header">
              <div>
                <h2>窗口位置</h2>
                <p>61 当前默认使用 3840 × 2160 全屏显示。</p>
              </div>
            </div>
            <div class="avatar-bounds-grid">
              <label>宽度<el-input-number v-model="avatarBounds.width" :min="1" :max="7680" controls-position="right" /></label>
              <label>高度<el-input-number v-model="avatarBounds.height" :min="1" :max="4320" controls-position="right" /></label>
              <label>X 坐标<el-input-number v-model="avatarBounds.x" :min="-7680" :max="7680" controls-position="right" /></label>
              <label>Y 坐标<el-input-number v-model="avatarBounds.y" :min="-4320" :max="4320" controls-position="right" /></label>
            </div>
            <el-button class="avatar-full-button" type="primary" plain :loading="avatarBusy === 'bounds'" :disabled="!avatarStatus.online" @click="applyAvatarBounds">应用位置和尺寸</el-button>
          </div>

          <div class="panel avatar-panel">
            <div class="panel-header">
              <div>
                <h2>播报测试</h2>
                <p>文本直接发送到数字人 SDK，不再经过普通 TTS。</p>
              </div>
            </div>
            <el-input v-model="avatarText" type="textarea" :rows="5" placeholder="输入数字人需要播报的内容" />
            <div class="avatar-speak-actions">
              <el-button type="primary" :loading="avatarBusy === 'say'" :disabled="!avatarStatus.online" @click="sayAvatar">开始播报</el-button>
              <el-button type="danger" plain :loading="avatarBusy === 'interrupt'" :disabled="!avatarStatus.online" @click="interruptAvatar">打断播报</el-button>
            </div>
          </div>
        </div>
      </section>

      <el-dialog v-model="tagHelpVisible" title="来源标签说明" width="720px" class="tag-help-dialog">
        <div class="tag-help-grid">
          <article v-for="item in answerSourceTags" :key="item.name" class="tag-help-item">
            <span class="conversation-source">{{ item.name }}</span>
            <div>
              <strong>{{ item.title }}</strong>
              <p>{{ item.desc }}</p>
            </div>
          </article>
        </div>
      </el-dialog>

      <section v-if="activeTab === 'devices'" class="page">
        <div class="panel">
          <div class="panel-header">
            <div>
              <h2>展示屏幕</h2>
              <p>屏幕按序号从小到大排列，展示端定时回传预览图。</p>
            </div>
            <el-button type="primary" @click="refreshDevices">刷新屏幕</el-button>
          </div>
          <el-empty v-if="sortedDevices.length === 0" description="暂无屏幕设备" />
          <div v-else class="screen-grid">
            <article
              v-for="screen in sortedDevices"
              :key="screen.deviceId || screen.id"
              class="screen-card"
              :class="{ offline: !screen.online }"
            >
              <div class="screen-card-preview">
                <img v-if="screen.snapshot?.url" :src="snapshotUrl(screen)" alt="" />
                <div v-else class="screen-card-empty">等待预览</div>
                <div class="screen-card-badge">{{ screen.screenNumber || '-' }}号屏</div>
                <div class="screen-card-status" :class="{ online: screen.online }">
                  <span></span>{{ screen.online ? '在线' : '离线' }}
                </div>
              </div>
              <div class="screen-card-body">
                <div>
                  <h3>{{ screen.name || '展示屏幕' }}</h3>
                  <p>{{ screen.snapshot?.updatedAt ? formatSnapshotTime(screen.snapshot.updatedAt) : '暂无截图' }}</p>
                </div>
                <div class="screen-metrics">
                  <div class="screen-meter" :class="{ danger: isCpuUnhealthy(screen.status) }">
                    <div class="screen-meter-head">
                      <span>CPU {{ formatPercent(screen.status?.cpuPercent) }}</span>
                    </div>
                    <div class="screen-meter-track">
                      <i :style="{ width: cpuBarWidth(screen.status) }"></i>
                    </div>
                  </div>
                  <div class="screen-meter" :class="{ danger: isMemoryUnhealthy(screen.status) }">
                    <div class="screen-meter-head">
                      <span>内存 {{ formatPercent(screen.status?.memoryPercent) }}</span>
                    </div>
                    <div class="screen-meter-track">
                      <i :style="{ width: memoryBarWidth(screen.status) }"></i>
                    </div>
                  </div>
                </div>
                <div class="screen-card-actions">
                  <el-button size="small" @click="openScreenSettings(screen)">设置</el-button>
                  <el-button size="small" :disabled="screen.type !== 'client' || !screen.online" @click="sendHome(screen)">回首页</el-button>
                <el-button size="small" :disabled="screen.type !== 'client' || !screen.online" @click="sendTestCommand(screen)">测试播报</el-button>
                </div>
              </div>
            </article>
          </div>
        </div>
      </section>

      <el-dialog v-model="screenSettingsDialog.visible" class="screen-settings-dialog" title="屏幕设置" width="420px">
        <el-form label-width="130px">
          <el-form-item label="屏幕名称">
            <el-input v-model="screenSettingsDialog.form.name" maxlength="80" placeholder="例如：入口左屏" />
          </el-form-item>
          <el-form-item label="画面同步间隔">
            <el-input-number v-model="screenSettingsDialog.form.snapshotIntervalSeconds" :min="1" :max="3600" :step="1" />
            <span class="form-unit">秒</span>
          </el-form-item>
          <el-form-item label="性能同步间隔">
            <el-input-number v-model="screenSettingsDialog.form.statusIntervalSeconds" :min="1" :max="3600" :step="1" />
            <span class="form-unit">秒</span>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="screenSettingsDialog.visible = false">取消</el-button>
          <el-button type="primary" :loading="screenSettingsDialog.saving" @click="saveScreenSettings">保存并同步</el-button>
        </template>
      </el-dialog>

      <section v-if="activeTab === 'knowledge'" class="page">
        <div class="knowledge-shell">
          <div class="knowledge-head">
            <div>
              <h2>知识库</h2>
              <p>文件用于屏幕检索和打开，QA 用于命中后直接回答，不再调用大模型。</p>
            </div>
            <div class="segmented">
              <button :class="{ active: knowledgeMode === 'files' }" @click="knowledgeMode = 'files'">文件库</button>
              <button :class="{ active: knowledgeMode === 'qa' }" @click="knowledgeMode = 'qa'">QA问答库</button>
              <button :class="{ active: knowledgeMode === 'webCandidates' }" @click="knowledgeMode = 'webCandidates'">
                联网候选<span v-if="pendingWebCandidateCount">（{{ pendingWebCandidateCount }}）</span>
              </button>
              <button :class="{ active: knowledgeMode === 'fallback' }" @click="knowledgeMode = 'fallback'">兜底库</button>
              <button :class="{ active: knowledgeMode === 'webSearch' }" @click="knowledgeMode = 'webSearch'">联网搜索</button>
              <button :class="{ active: knowledgeMode === 'obsidian' }" @click="knowledgeMode = 'obsidian'">Obsidian</button>
            </div>
          </div>

          <div v-if="knowledgeMode === 'files'" class="kb-panel">
            <div class="kb-toolbar">
              <div>
                <strong>知识库文件</strong>
                <span>同步中控 media 目录，缩略图和讲解词会一并显示。</span>
              </div>
              <div class="filter-row">
                <input ref="fileUploadInput" class="hidden-file-input" type="file" multiple accept=".ppt,.pptx,.mp4,.webm,.avi,.mov,.mkv,.png,.jpg,.jpeg,.gif,.webp,.bmp" @change="handleUploadFiles" />
                <el-button type="primary" :loading="uploadingFiles" @click="triggerFileUpload">上传文件</el-button>
                <el-input v-model="mediaKeyword" placeholder="搜索标题或文件名" clearable @clear="resetMediaPage" @keyup.enter="resetMediaPage" />
                <el-select v-model="mediaType" style="width: 130px" @change="resetMediaPage">
                  <el-option label="全部" value="all" />
                  <el-option label="PPT" value="ppt" />
                  <el-option label="视频" value="video" />
                  <el-option label="图片" value="image" />
                </el-select>
              </div>
            </div>

            <div class="kb-table">
              <div class="kb-row kb-row-head">
                <span>#</span>
                <span>缩略图</span>
                <span>标题</span>
                <span>文件名</span>
                <span>类型</span>
                <span>页数</span>
                <span>来源</span>
                <span>讲解词</span>
                <span>操作</span>
              </div>
              <div v-if="filteredMediaFiles.length === 0" class="empty-line">暂无匹配文件</div>
              <div v-for="(file, index) in pagedMediaFiles" :key="file.id || file.name" class="kb-row">
                <span class="muted">{{ mediaRowNumber(index) }}</span>
                <span>
                  <img v-if="file.thumbnailUrl" class="thumb" :src="file.thumbnailUrl" alt="" />
                  <i v-else class="thumb placeholder">{{ typeLabel(file.type) }}</i>
                </span>
                <span class="strong">{{ file.title || file.name }}</span>
                <span class="file-name">{{ file.name }}</span>
                <span><b class="tag blue">{{ typeLabel(file.type) }}</b></span>
                <span class="muted">{{ pageCountLabel(file) }}</span>
                <span><b class="tag amber">{{ file.existsLocal ? '本地文件' : '配置库' }}</b></span>
                <span><b class="tag" :class="file.hasCommentary ? 'green' : 'gray'">{{ file.hasCommentary ? '有' : '无' }}</b></span>
                <span><el-button size="small" @click="openCommentaryEditor(file)">编辑</el-button></span>
              </div>
            </div>
            <div v-if="filteredMediaFiles.length > mediaPageSize" class="table-pagination">
              <el-pagination
                v-model:current-page="mediaPage"
                :page-size="mediaPageSize"
                :total="filteredMediaFiles.length"
                layout="prev, pager, next, jumper, total"
                background
              />
            </div>
          </div>

          <div v-else-if="knowledgeMode === 'qa'" class="kb-panel qa-panel">
            <div class="kb-toolbar">
              <div>
                <strong>QA问答库</strong>
                <span>适合固定口径。用户提问命中后，屏幕直接显示这里的答案。</span>
              </div>
              <div class="filter-row">
                <el-input v-model="qaKeyword" placeholder="搜索问题、答案或关键词" clearable @clear="resetQaPage" @keyup.enter="resetQaPage" />
                <el-button type="primary" @click="openQaEditor()">新增问答</el-button>
              </div>
            </div>

            <div class="qa-bulkbar">
              <el-checkbox
                :model-value="qaPageSelectionState.checked"
                :indeterminate="qaPageSelectionState.indeterminate"
                :disabled="pagedQaItems.length === 0"
                @change="toggleCurrentQaPageSelection"
              >
                选择本页
              </el-checkbox>
              <span>已选 {{ selectedQaIds.length }} 条</span>
              <el-button size="small" :disabled="selectedQaIds.length === 0 || qaBatching" @click="batchUpdateQa(true)">批量启用</el-button>
              <el-button size="small" :disabled="selectedQaIds.length === 0 || qaBatching" @click="batchUpdateQa(false)">批量停用</el-button>
              <el-button size="small" type="danger" plain :disabled="selectedQaIds.length === 0 || qaBatching" @click="batchRemoveQa">批量删除</el-button>
            </div>

            <div class="qa-list">
              <article v-if="filteredQaItems.length === 0" class="empty-line">暂无 QA 问答</article>
              <article v-for="item in pagedQaItems" :key="item.id" class="qa-card" :class="{ disabled: item.enabled === false, selected: selectedQaIdSet.has(item.id) }">
                <el-checkbox
                  class="qa-select"
                  :model-value="selectedQaIdSet.has(item.id)"
                  @change="checked => toggleQaSelection(item.id, checked)"
                />
                <div class="qa-card-main">
                  <div class="qa-question">
                    <span class="status-pill" :class="{ off: item.enabled === false }">{{ item.enabled === false ? '停用' : '启用' }}</span>
                    <strong>{{ item.question }}</strong>
                  </div>
                  <p>{{ item.answer }}</p>
                  <div class="keyword-line">
                    <b v-for="word in item.keywords || []" :key="word">{{ word }}</b>
                    <span v-if="!(item.keywords || []).length">未设置关键词</span>
                  </div>
                </div>
                <div class="qa-actions">
                  <el-switch
                    :model-value="item.enabled !== false"
                    @change="value => toggleQa(item, value)"
                  />
                  <el-button size="small" @click="openQaEditor(item)">编辑</el-button>
                  <el-button size="small" type="danger" plain @click="removeQa(item)">删除</el-button>
                </div>
              </article>
            </div>
            <div v-if="filteredQaItems.length > qaPageSize" class="table-pagination">
              <el-pagination
                v-model:current-page="qaPage"
                :page-size="qaPageSize"
                :total="filteredQaItems.length"
                layout="prev, pager, next, jumper, total"
                background
              />
            </div>
          </div>

          <div v-else-if="knowledgeMode === 'fallback'" class="kb-panel fallback-panel">
            <div class="kb-toolbar">
              <div>
                <strong>兜底话术库</strong>
                <span>QA 未命中时按优先级和量化规则匹配，命中后直接展示并播报已审核的话术。</span>
              </div>
              <div class="filter-row">
                <el-input v-model="fallbackKeyword" placeholder="搜索名称、分类、触发词或话术" clearable @clear="resetFallbackPage" @keyup.enter="resetFallbackPage" />
                <el-select v-model="fallbackCategory" style="width: 150px" @change="resetFallbackPage">
                  <el-option label="全部分类" value="all" />
                  <el-option v-for="category in fallbackCategories" :key="category" :label="category" :value="category" />
                </el-select>
                <el-button type="primary" @click="openFallbackEditor()">新增规则</el-button>
              </div>
            </div>

            <div class="fallback-testbar">
              <el-input v-model="fallbackTestText" placeholder="输入一句模拟问题，检查会命中哪条兜底规则" clearable @keyup.enter="testFallbackRule" />
              <el-select v-model="fallbackTestTarget" style="width: 130px">
                <el-option label="全部屏幕" value="all" />
                <el-option v-for="screen in onlineScreens" :key="screen.deviceId || screen.id" :label="`${screen.screenNumber || '-'}号屏`" :value="String(screen.screenNumber)" />
              </el-select>
              <el-button :loading="fallbackTesting" @click="testFallbackRule">测试匹配</el-button>
              <div v-if="fallbackTestResult" class="fallback-test-result" :class="{ missed: !fallbackTestResult.match }">
                <template v-if="fallbackTestResult.match">
                  命中：{{ fallbackTestResult.match.item.name }}，得分 {{ fallbackTestResult.match.score }}，关键词 {{ (fallbackTestResult.match.matchedKeywords || []).join('、') }}
                </template>
                <template v-else>未命中任何启用规则</template>
              </div>
            </div>

            <div class="fallback-summary">
              <span>共 {{ fallbackRules.length }} 条</span>
              <span>启用 {{ enabledFallbackCount }} 条</span>
              <span>页面规则 {{ filteredFallbackRules.length }} 条</span>
            </div>

            <div class="fallback-list">
              <article v-if="filteredFallbackRules.length === 0" class="empty-line">暂无兜底规则</article>
              <article v-for="item in pagedFallbackRules" :key="item.id" class="fallback-card" :class="{ disabled: item.enabled === false }">
                <div class="fallback-card-main">
                  <div class="fallback-card-title">
                    <span class="status-pill" :class="{ off: item.enabled === false }">{{ item.enabled === false ? '停用' : '启用' }}</span>
                    <b class="tag blue">{{ item.category }}</b>
                    <strong>{{ item.name }}</strong>
                  </div>
                  <p>{{ item.answer }}</p>
                  <div class="keyword-line">
                    <b v-for="word in item.keywords || []" :key="word">{{ word }}</b>
                  </div>
                  <div class="fallback-metrics">
                    <span>优先级 {{ item.priority }}</span>
                    <span>阈值 {{ item.threshold }}</span>
                    <span>{{ item.matchMode === 'all' ? '全部命中' : `至少 ${item.minKeywordHits} 个词` }}</span>
                    <span>{{ fallbackScopeLabel(item) }}</span>
                    <span>{{ fallbackActionLabel(item.actionType) }}</span>
                  </div>
                </div>
                <div class="qa-actions">
                  <el-switch :model-value="item.enabled !== false" @change="value => toggleFallback(item, value)" />
                  <el-button size="small" @click="openFallbackEditor(item)">编辑</el-button>
                  <el-button size="small" type="danger" plain @click="removeFallback(item)">删除</el-button>
                </div>
              </article>
            </div>
            <div v-if="filteredFallbackRules.length > fallbackPageSize" class="table-pagination">
              <el-pagination v-model:current-page="fallbackPage" :page-size="fallbackPageSize" :total="filteredFallbackRules.length" layout="prev, pager, next, jumper, total" background />
            </div>
          </div>

          <div v-else-if="knowledgeMode === 'webSearch'" class="kb-panel fallback-panel">
            <div class="kb-toolbar">
              <div>
                <strong>联网搜索规则</strong>
                <span>强制联网规则会在日期时间和本地问答前执行；普通联网规则在本地问答未命中后执行，并保留来源与路由日志。</span>
              </div>
              <div class="filter-row">
                <el-input v-model="webSearchKeyword" placeholder="搜索名称、分类、触发词或模板" clearable @clear="resetWebSearchPage" @keyup.enter="resetWebSearchPage" />
                <el-select v-model="webSearchCategory" style="width: 150px" @change="resetWebSearchPage">
                  <el-option label="全部分类" value="all" />
                  <el-option v-for="category in webSearchCategories" :key="category" :label="category" :value="category" />
                </el-select>
                <el-button type="primary" @click="openWebSearchEditor()">新增规则</el-button>
              </div>
            </div>

            <div class="fallback-testbar">
              <el-input v-model="webSearchTestText" placeholder="输入一句模拟问题，检查是否触发联网搜索" clearable @keyup.enter="testWebSearchRule" />
              <el-button :loading="webSearchTesting" @click="testWebSearchRule">测试搜索</el-button>
              <div v-if="webSearchTestResult" class="fallback-test-result" :class="{ missed: !webSearchTestResult.matched }">
                <template v-if="webSearchTestResult.matched">
                  命中：{{ webSearchTestResult.intent?.item?.name || '前置联网路由' }}（{{ webSearchTestResult.route?.mode || 'PREFER_WEB' }}），来源 {{ webSearchTestResult.result?.context?.sources?.length || 0 }} 条
                </template>
                <template v-else>未命中任何启用规则</template>
              </div>
            </div>

            <div class="fallback-summary">
              <span>共 {{ webSearchRules.length }} 条</span>
              <span>启用 {{ enabledWebSearchCount }} 条</span>
              <span>页面规则 {{ filteredWebSearchRules.length }} 条</span>
              <span>统计最近 {{ webSearchStatsWindow?.scannedEvents || 0 }} 条请求记录</span>
            </div>

            <div class="fallback-list">
              <article v-if="filteredWebSearchRules.length === 0" class="empty-line">暂无联网搜索规则</article>
              <article v-for="item in pagedWebSearchRules" :key="item.id" class="fallback-card" :class="{ disabled: item.enabled === false }">
                <div class="fallback-card-main">
                  <div class="fallback-card-title">
                    <span class="status-pill" :class="{ off: item.enabled === false }">{{ item.enabled === false ? '停用' : '启用' }}</span>
                    <b class="tag blue">{{ item.category }}</b>
                    <strong>{{ item.name }}</strong>
                  </div>
                  <p>搜索模板：{{ item.queryTemplate || '{question}' }}</p>
                  <div class="keyword-line">
                    <b v-for="word in item.keywords || []" :key="word">{{ word }}</b>
                  </div>
                  <p v-if="item.matchPatterns?.length">正则形态：{{ item.matchPatterns.length }} 条</p>
                  <div class="fallback-metrics">
                    <span>{{ item.routeMode === 'MUST_WEB' ? '强制联网' : '普通联网' }}</span>
                    <span>优先级 {{ item.priority }}</span>
                    <span>阈值 {{ item.threshold }}</span>
                    <span>{{ item.matchMode === 'all' ? '全部命中' : `至少 ${item.minKeywordHits} 个词` }}</span>
                    <span>结果 {{ item.maxResults || 5 }} 条</span>
                    <span>{{ item.recencyDays ? `近 ${item.recencyDays} 天` : '不限时间' }}</span>
                  </div>
                  <div class="fallback-metrics rule-performance">
                    <span>实际命中 {{ item.stats?.matched || 0 }}</span>
                    <span>成功回答 {{ item.stats?.answered || 0 }}</span>
                    <span>未回答 {{ item.stats?.notAnswered || 0 }}</span>
                    <span>技术失败 {{ item.stats?.failed || 0 }}</span>
                    <span>成功率 {{ item.stats?.successRate == null ? '暂无' : `${item.stats.successRate}%` }}</span>
                  </div>
                </div>
                <div class="qa-actions">
                  <el-switch :model-value="item.enabled !== false" @change="value => toggleWebSearchRule(item, value)" />
                  <el-button size="small" @click="openWebSearchEditor(item)">编辑</el-button>
                  <el-button size="small" type="danger" plain @click="removeWebSearchRule(item)">删除</el-button>
                </div>
              </article>
            </div>
            <div v-if="filteredWebSearchRules.length > webSearchPageSize" class="table-pagination">
              <el-pagination v-model:current-page="webSearchPage" :page-size="webSearchPageSize" :total="filteredWebSearchRules.length" layout="prev, pager, next, jumper, total" background />
            </div>
          </div>

          <div v-else-if="knowledgeMode === 'obsidian'" class="kb-panel obsidian-panel">
            <div class="kb-toolbar">
              <div>
                <strong>Obsidian 内容母仓</strong>
                <span>扫描 Markdown frontmatter，构建中控运行索引；现场问答直接使用 Markdown 内容。</span>
              </div>
              <div class="filter-row obsidian-actions">
                <el-input v-model="obsidianVaultPath" placeholder="Obsidian库路径，例如 D:\\ExhibitionVault" clearable />
                <el-button type="primary" :loading="obsidianSyncing" @click="syncObsidian">同步 Obsidian</el-button>
                <el-input-number v-model="obsidianAnalyzeLimit" :min="1" :max="30" controls-position="right" />
                <el-button :loading="obsidianAnalyzing" @click="analyzeObsidian">AI分析草稿</el-button>
              </div>
            </div>

            <div class="obsidian-analysis-board">
              <div class="analysis-board-head">
                <div>
                  <strong>已分析 Markdown</strong>
                  <span>展示已写入 Markdown 的 AI 摘要、标签和讲解词草稿。</span>
                </div>
                <div class="filter-row">
                  <el-input v-model="obsidianAnalysisKeyword" placeholder="搜索标题、路径或摘要" clearable @keyup.enter="searchObsidianAnalyses" @clear="searchObsidianAnalyses" />
                  <el-button :loading="obsidianAnalysesLoading" @click="searchObsidianAnalyses">搜索</el-button>
                </div>
              </div>
              <el-empty v-if="!obsidianAnalysesLoading && !obsidianAnalyses.length" description="暂无已分析内容" />
              <div v-else class="analysis-card-grid">
                <article v-for="item in obsidianAnalyses" :key="item.id" class="analysis-card" @click="openObsidianAnalysisDetail(item)">
                  <div class="analysis-card-top">
                    <strong>{{ item.title }}</strong>
                  </div>
                  <div class="analysis-meta">
                    <span>文件ID</span>
                    <b>{{ item.resourceId || item.id || '-' }}</b>
                  </div>
                  <div class="analysis-meta">
                    <span>文件类型</span>
                    <b>{{ item.contentType || item.type || '-' }}</b>
                  </div>
                  <div class="analysis-tags" aria-label="标签">
                    <b v-for="tag in (item.tags || []).slice(0, 5)" :key="tag">{{ tag }}</b>
                    <b v-if="(item.tags || []).length > 5">+{{ (item.tags || []).length - 5 }}</b>
                    <em v-if="!(item.tags || []).length">暂无标签</em>
                  </div>
                  <button type="button" class="analysis-detail-link" @click.stop="openObsidianAnalysisDetail(item)">查看详情</button>
                </article>
              </div>
              <div v-if="obsidianAnalysesTotal > obsidianAnalysesPageSize" class="analysis-pagination">
                <el-pagination
                  v-model:current-page="obsidianAnalysesPage"
                  :page-size="obsidianAnalysesPageSize"
                  :total="obsidianAnalysesTotal"
                  layout="prev, pager, next, jumper, total"
                  background
                  @current-change="refreshObsidianAnalyses"
                />
              </div>
            </div>

            <div class="obsidian-summary">
              <div>
                <span>Markdown 索引</span>
                <strong>{{ knowledgeStatus?.indexPath || '-' }}</strong>
              </div>
              <div>
                <span>上次系统同步</span>
                <strong>{{ formatTime(knowledgeStatus?.lastSyncAt) }}</strong>
              </div>
              <div>
                <span>上次 Obsidian 同步</span>
                <strong>{{ formatTime(knowledgeStatus?.lastObsidianSyncAt) }}</strong>
              </div>
            </div>

            <div class="obsidian-counts">
              <article v-for="item in knowledgeCountCards" :key="item.key">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </article>
            </div>

            <div v-if="obsidianLastImport" class="obsidian-result">
              <strong>本次导入结果</strong>
              <span>Markdown {{ obsidianLastImport.files }} 个</span>
              <span>知识对象 {{ obsidianLastImport.knowledgeObjects }} 个</span>
              <span>QA {{ obsidianLastImport.qaItems }} 条</span>
              <span>专场 {{ obsidianLastImport.scenes }} 个</span>
              <span>跳过 {{ obsidianLastImport.skipped }} 个</span>
            </div>

            <div v-if="obsidianLastAnalysis" class="obsidian-result analysis-result">
              <strong>AI分析结果</strong>
              <span>模型 {{ obsidianLastAnalysis.model }}</span>
              <span>本次处理 {{ obsidianLastAnalysis.analyzed }} 个</span>
              <span>成功 {{ obsidianLastAnalysis.success }} 个</span>
              <span>失败 {{ obsidianLastAnalysis.failed }} 个</span>
              <div class="analysis-list">
                <article v-for="item in obsidianLastAnalysis.results" :key="item.file" :class="{ failed: !item.ok }">
                  <b>{{ item.ok ? '完成' : '失败' }}</b>
                  <span>{{ item.file }}</span>
                  <small>{{ item.ok ? item.summary : item.error }}</small>
                </article>
              </div>
            </div>
          </div>
        </div>
      </section>

      <el-dialog v-model="obsidianDetailDialog.visible" title="AI分析详情" width="860px" class="analysis-detail-dialog">
        <div v-if="obsidianDetailDialog.item" class="analysis-detail">
          <header>
            <strong>{{ obsidianDetailDialog.item.title }}</strong>
            <span>{{ obsidianDetailDialog.item.contentType || obsidianDetailDialog.item.type }} · {{ obsidianDetailDialog.item.status }}</span>
          </header>
          <div class="detail-meta">
            <span>文件路径</span>
            <b>{{ obsidianDetailDialog.item.file || '-' }}</b>
          </div>
          <div class="detail-meta">
            <span>资源ID</span>
            <b>{{ obsidianDetailDialog.item.resourceId || '-' }}</b>
          </div>
          <div class="detail-meta">
            <span>基础信息</span>
            <b>{{ [
              obsidianDetailDialog.item.serial ? `序号 ${obsidianDetailDialog.item.serial}` : '',
              obsidianDetailDialog.item.planner ? `策划：${obsidianDetailDialog.item.planner}` : '',
              obsidianDetailDialog.item.year || '',
              obsidianDetailDialog.item.caseRegion || '',
              obsidianDetailDialog.item.sourceFrom ? `出处：${obsidianDetailDialog.item.sourceFrom}` : '',
            ].filter(Boolean).join(' · ') || '-' }}</b>
          </div>
          <section>
            <h3>摘要</h3>
            <p>{{ obsidianDetailDialog.item.summary || '暂无摘要' }}</p>
          </section>
          <section>
            <h3>解析结果（qwen-vl-plus）</h3>
            <p>{{ obsidianDetailDialog.item.analysisResult || '暂无解析结果' }}</p>
          </section>
          <section>
            <h3>标签</h3>
            <div class="detail-tags">
              <b v-for="tag in obsidianDetailDialog.item.tags || []" :key="tag">{{ tag }}</b>
              <span v-if="!(obsidianDetailDialog.item.tags || []).length">暂无标签</span>
            </div>
          </section>
          <section>
            <h3>讲解词草稿</h3>
            <p class="detail-commentary">{{ obsidianDetailDialog.item.commentaryDraft || '暂无讲解词草稿' }}</p>
          </section>
          <section>
            <h3>FAQ</h3>
            <div v-if="(obsidianDetailDialog.item.faqs || []).length" class="detail-faqs">
              <article v-for="(faq, index) in obsidianDetailDialog.item.faqs" :key="`${faq.question}-${index}`">
                <strong>问：{{ faq.question }}</strong>
                <p>答：{{ faq.answer }}</p>
              </article>
            </div>
            <p v-else>暂无 FAQ</p>
          </section>
        </div>
      </el-dialog>

      <section v-if="activeTab === 'knowledge' && knowledgeMode === 'webCandidates'" class="web-candidate-page knowledge-embedded-candidates">
        <div class="stats-grid web-candidate-stats">
          <article class="stat-card">
            <span>待审核候选</span>
            <strong>{{ pendingWebCandidateCount }}</strong>
            <em>尚未进入正式 QA</em>
          </article>
          <article class="stat-card">
            <span>可提升</span>
            <strong>{{ eligibleWebCandidateCount }}</strong>
            <em>已满足对应候选类型的分级条件</em>
          </article>
          <article class="stat-card">
            <span>已提升</span>
            <strong>{{ promotedWebCandidateCount }}</strong>
            <em>已由管理员确认进入 QA</em>
          </article>
          <article class="stat-card">
            <span>时效候选</span>
            <strong>{{ timeSensitiveWebCandidateCount }}</strong>
            <em>到期后不会继续命中</em>
          </article>
        </div>

        <div class="panel">
          <div class="panel-header">
            <div>
              <h2>联网回答候选池</h2>
              <p>人工核验任一来源后可立即手动提升；若在前两次完成核验，第 3 次出现相同答案时将自动进入 QA。</p>
            </div>
            <el-button :loading="webCandidatesLoading" @click="refreshWebQaCandidates">刷新候选</el-button>
          </div>

          <div class="web-candidate-toolbar">
            <el-input
              v-model="webCandidateKeyword"
              placeholder="搜索问题、答案或来源"
              clearable
            />
            <el-select v-model="webCandidateStatus" style="width: 150px">
              <el-option label="全部状态" value="all" />
              <el-option label="待审核" value="CANDIDATE" />
              <el-option label="已提升" value="PROMOTED" />
              <el-option label="已拒绝" value="REJECTED" />
            </el-select>
            <el-select v-model="webCandidateFreshness" style="width: 160px">
              <el-option label="全部时效类型" value="all" />
              <el-option label="稳定知识" value="STABLE" />
              <el-option label="有年份/时限" value="TIME_BOUND" />
              <el-option label="时效知识" value="TIME_SENSITIVE" />
            </el-select>
          </div>

          <el-empty
            v-if="!webCandidatesLoading && filteredWebQaCandidates.length === 0"
            description="暂无符合条件的联网候选"
          />
          <div v-else class="web-candidate-list">
            <article
              v-for="item in filteredWebQaCandidates"
              :key="item.id"
              class="web-candidate-card"
              :class="{ eligible: item.eligibleForPromotion, promoted: item.status === 'PROMOTED', rejected: item.status === 'REJECTED' }"
            >
              <div class="web-candidate-main">
                <div class="web-candidate-title">
                  <span class="status-pill" :class="webCandidateStatusClass(item)">{{ webCandidateStatusLabel(item) }}</span>
                  <span class="candidate-type" :class="{ volatile: item.candidateType === 'PUBLIC_VOLATILE' }">
                    {{ webCandidateTypeLabel(item) }}
                  </span>
                  <strong>{{ item.question }}</strong>
                </div>
                <p>{{ item.answer }}</p>
                <div class="candidate-criteria">
                  <span :class="{ done: Number(item.useCount) >= 3 }">使用 {{ item.useCount || 0 }}/3（自动提升阈值）</span>
                  <span :class="{ done: item.knowledgeRequirementSatisfied !== false }">
                    {{ item.eligibilityPolicy?.knowledgeRequired
                      ? (item.knowledgeSupported ? '知识库已支持' : '缺少知识库支持')
                      : '无需知识库支持' }}
                  </span>
                  <span :class="{ done: Number(item.sourceCount) >= 1 }">已有来源 {{ item.sourceCount || 0 }}</span>
                  <span :class="{ done: item.sourceRequirementSatisfied !== false }">
                    可靠来源 {{ item.reliableSourceCount || 0 }}
                  </span>
                  <span :class="{ expired: isWebCandidateExpired(item) }">
                    {{ isWebCandidateExpired(item) ? '已过期' : `有效至 ${formatCandidateDate(item.expiresAt)}` }}
                  </span>
                </div>
                <div class="candidate-sources">
                  <a
                    v-for="source in (item.sources || []).slice(0, 3)"
                    :key="source.url"
                    :href="source.url"
                    target="_blank"
                    rel="noreferrer"
                  >{{ source.title || source.domain || source.url }}</a>
                  <span v-if="!(item.sources || []).length">暂无可展示来源</span>
                </div>
              </div>
              <div class="web-candidate-actions">
                <el-button size="small" @click="openWebCandidateReview(item)">查看详情</el-button>
                <el-button
                  size="small"
                  type="primary"
                  :disabled="!item.eligibleForPromotion || item.status !== 'CANDIDATE' || isWebCandidateExpired(item)"
                  @click="openWebCandidateReview(item)"
                >审核提升</el-button>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  :disabled="item.status !== 'CANDIDATE'"
                  :loading="webCandidateActionId === item.id"
                  @click="rejectWebCandidate(item)"
                >拒绝</el-button>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'specials'" class="page">
        <div class="special-layout">
          <div class="panel">
            <div class="panel-header">
              <h2>专场列表</h2>
              <el-button type="primary" @click="createSpecial">新增专场</el-button>
            </div>
            <div class="special-list">
              <button
                v-for="item in specials"
                :key="item.id"
                :class="['special-item', { active: selectedSpecial?.id === item.id }]"
                @click="selectSpecial(item)"
              >
                <span>{{ item.name }}</span>
                <small>{{ specialSummary(item) }}</small>
              </button>
            </div>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <h2>专场配置</h2>
                <p>切换专场后，各屏幕会自动打开这里配置的默认文件。</p>
              </div>
              <div class="top-actions">
                <el-button :disabled="!selectedSpecial" @click="activateSpecial">立即切换</el-button>
                <el-button type="primary" :disabled="!selectedSpecial" @click="saveSpecial">保存配置</el-button>
              </div>
            </div>

            <el-empty v-if="!selectedSpecial" description="请选择或新建一个专场" />
            <div v-else class="special-editor">
              <el-form label-width="90px">
                <el-form-item label="专场名称">
                  <el-input v-model="selectedSpecial.name" placeholder="例如：思德专场" />
                </el-form-item>
                <el-form-item label="备注">
                  <el-input v-model="selectedSpecial.description" placeholder="可选" />
                </el-form-item>
              </el-form>

              <div class="assignment-table">
                <div class="assignment-head">
                  <span>屏幕</span>
                  <span>专场文件</span>
                </div>
                <div v-for="row in screenAssignments" :key="row.screenNumber" class="assignment-row">
                  <span>{{ row.screenNumber }} 号屏</span>
                  <div class="assignment-files">
                    <el-select
                      v-model="row.fileNames"
                      multiple
                      filterable
                      clearable
                      placeholder="选择该屏专场文件，可多选"
                    >
                      <el-option
                        v-for="file in mediaFiles"
                        :key="file.name"
                        :label="`${typeLabel(file.type)} | ${file.title || file.name}`"
                        :value="file.name"
                      />
                    </el-select>
                    <ol v-if="row.fileNames?.length" class="assignment-file-list">
                      <li v-for="fileName in row.fileNames" :key="fileName">
                        {{ assignmentFileLabel(fileName) }}
                      </li>
                    </ol>
                    <div v-else class="assignment-empty">未配置文件</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'voice'" class="page">
        <div class="panel"><h2>播报音色</h2><p>音色设置已统一到数字人管理。</p><el-button type="primary" @click="activeTab = 'avatar'">打开数字人语音设置</el-button></div>
      </section>
    </main>

    <el-dialog v-model="commentaryDialog.visible" title="编辑讲解词" width="920px" class="commentary-dialog">
      <div class="commentary-editor">
        <div class="commentary-file">
          <strong>{{ commentaryDialog.file?.title || commentaryDialog.file?.name }}</strong>
          <span>{{ commentaryDialog.file?.name }}</span>
        </div>
        <template v-if="isPptCommentaryFile">
          <div class="commentary-page-head">
            <div class="page-count-control">
              <span>PPT页数</span>
              <el-input-number
                v-model="commentaryDialog.pageCount"
                :min="1"
                :max="200"
                :step="1"
                @change="resizeCommentaryPages"
              />
              <em>按页设置，保存为 P1、P2、P3 讲解词。</em>
            </div>
            <div class="segmented commentary-level-switch">
              <button
                v-for="level in commentaryLevels"
                :key="level.key"
                :class="{ active: commentaryDialog.level === level.key }"
                type="button"
                @click="commentaryDialog.level = level.key"
              >
                {{ level.label }}
              </button>
            </div>
          </div>
          <div class="commentary-pages">
            <div v-for="page in commentaryDialog.pages" :key="page.page" class="commentary-page">
              <strong>P{{ page.page }} {{ activeCommentaryLevelLabel }}讲解词</strong>
              <el-input
                v-model="page.levels[commentaryDialog.level]"
                type="textarea"
                :rows="5"
                :placeholder="`输入第 ${page.page} 页的${activeCommentaryLevelLabel}讲解词`"
              />
            </div>
          </div>
        </template>
        <el-input
          v-else
          v-model="commentaryDialog.text"
          type="textarea"
          :rows="12"
          placeholder="输入这份文件的讲解词。保存后，展示端下次加载讲解词时会使用新内容。"
        />
      </div>
      <template #footer>
        <el-button @click="commentaryDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="commentaryDialog.saving" @click="saveCommentary">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="qaDialog.visible" :title="qaDialog.form.id ? '编辑QA问答' : '新增QA问答'" width="760px">
      <el-form label-width="90px" class="qa-form">
        <el-form-item label="问题">
          <el-input v-model="qaDialog.form.question" placeholder="例如：思德科技有哪些核心能力？" />
        </el-form-item>
        <el-form-item label="答案">
          <el-input v-model="qaDialog.form.answer" type="textarea" :rows="8" placeholder="输入屏幕上直接展示和播报的标准答案" />
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="qaDialog.form.keywordsText" type="textarea" :rows="3" placeholder="可用逗号或换行分隔，用于提高命中率" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="qaDialog.form.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="qaDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="qaDialog.saving" @click="saveQa">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="webCandidateReview.visible"
      title="审核联网候选"
      width="820px"
      class="web-candidate-dialog"
    >
      <div v-if="webCandidateReview.item" class="web-candidate-review">
        <div class="review-summary">
          <span class="status-pill" :class="webCandidateStatusClass(webCandidateReview.item)">
            {{ webCandidateStatusLabel(webCandidateReview.item) }}
          </span>
          <span>{{ webCandidateTypeLabel(webCandidateReview.item) }}</span>
          <span>使用 {{ webCandidateReview.item.useCount || 0 }}/3（第 3 次自动提升）</span>
          <span>{{ webCandidateReview.item.eligibilityPolicy?.knowledgeRequired
            ? (webCandidateReview.item.knowledgeSupported ? '知识库已支持' : '缺少知识库支持')
            : '无需知识库支持' }}</span>
          <span>已有来源 {{ webCandidateReview.item.sourceCount || 0 }}</span>
          <span>可靠来源 {{ webCandidateReview.item.reliableSourceCount || 0 }}</span>
          <span :class="{ expired: isWebCandidateExpired(webCandidateReview.item) }">
            {{ isWebCandidateExpired(webCandidateReview.item) ? '候选已过期' : `有效至 ${formatCandidateDate(webCandidateReview.item.expiresAt)}` }}
          </span>
        </div>
        <el-alert
          v-if="webCandidateUnmetReasons(webCandidateReview.item).length"
          :title="`暂不可提升：${webCandidateUnmetReasons(webCandidateReview.item).join('；')}`"
          type="warning"
          :closable="false"
          show-icon
        />
        <el-form label-width="92px" class="qa-form">
          <el-form-item label="正式问题">
            <el-input v-model="webCandidateReview.question" placeholder="确认进入 QA 后使用的标准问题" />
          </el-form-item>
          <el-form-item label="正式答案">
            <el-input
              v-model="webCandidateReview.answer"
              type="textarea"
              :rows="8"
              placeholder="请核对事实、时态和表达后再提升"
            />
          </el-form-item>
        </el-form>
        <section class="review-sources">
          <h3>联网来源</h3>
          <p v-if="webCandidateReview.item.eligibilityPolicy?.sourceRequired">
            提升条件：{{ webCandidateReview.item.eligibilityPolicy.sourceRule }}
          </p>
          <div
            v-for="source in webCandidateReview.item.sources || []"
            :key="source.url"
            class="review-source-row"
          >
            <a :href="source.url" target="_blank" rel="noreferrer">
              <strong>{{ source.title || source.domain || source.url }}</strong>
              <small>{{ source.domain || source.url }}</small>
              <small>{{ webCandidateSourceLabel(source) }}</small>
            </a>
            <el-button
              size="small"
              :type="source.manuallyVerified ? 'success' : 'primary'"
              plain
              :loading="webCandidateActionId === `${webCandidateReview.item.id}:${source.url}`"
              @click="toggleWebCandidateSourceVerification(webCandidateReview.item, source)"
            >{{ source.manuallyVerified ? '取消人工核验' : '标记为已核验可靠' }}</el-button>
          </div>
          <p v-if="!(webCandidateReview.item.sources || []).length">暂无可展示来源</p>
        </section>
      </div>
      <template #footer>
        <el-button @click="webCandidateReview.visible = false">关闭</el-button>
        <el-button
          type="danger"
          plain
          :disabled="webCandidateReview.item?.status !== 'CANDIDATE'"
          :loading="webCandidateReview.saving && webCandidateActionId === webCandidateReview.item?.id"
          @click="rejectWebCandidate(webCandidateReview.item)"
        >拒绝候选</el-button>
        <el-button
          type="primary"
          :disabled="!canPromoteWebCandidate(webCandidateReview.item)"
          :loading="webCandidateReview.saving"
          @click="promoteWebCandidate"
        >确认提升到 QA</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="fallbackDialog.visible" :title="fallbackDialog.form.id ? '编辑兜底规则' : '新增兜底规则'" width="860px" class="fallback-dialog">
      <el-form label-width="110px" class="qa-form">
        <div class="fallback-form-grid">
          <el-form-item label="规则名称">
            <el-input v-model="fallbackDialog.form.name" placeholder="例如：价格与商务咨询" />
          </el-form-item>
          <el-form-item label="分类">
            <el-select v-model="fallbackDialog.form.category" filterable allow-create default-first-option placeholder="选择或输入分类">
              <el-option v-for="category in fallbackCategories" :key="category" :label="category" :value="category" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="兜底话术">
          <el-input v-model="fallbackDialog.form.answer" type="textarea" :rows="5" placeholder="输入命中后屏幕直接展示并播报的完整话术" />
        </el-form-item>
        <div class="fallback-form-grid">
          <el-form-item label="触发词">
            <el-input v-model="fallbackDialog.form.keywordsText" type="textarea" :rows="4" placeholder="逗号或换行分隔，例如：多少钱、报价、预算" />
          </el-form-item>
          <el-form-item label="排除词">
            <el-input v-model="fallbackDialog.form.excludeKeywordsText" type="textarea" :rows="4" placeholder="包含这些词时不触发，可留空" />
          </el-form-item>
        </div>
        <el-form-item label="问题形态">
          <el-input v-model="webSearchDialog.form.matchPatternsText" type="textarea" :rows="3" placeholder="每行一个正则；可不填。例：第\\s*\\d+\\s*届.{0,20}(最佳|获奖|得主)" />
        </el-form-item>
        <div class="fallback-form-grid three">
          <el-form-item label="匹配方式">
            <el-select v-model="fallbackDialog.form.matchMode">
              <el-option label="任意触发词" value="any" />
              <el-option label="全部触发词" value="all" />
            </el-select>
          </el-form-item>
          <el-form-item label="最少命中">
            <el-input-number v-model="fallbackDialog.form.minKeywordHits" :min="1" :max="20" :disabled="fallbackDialog.form.matchMode === 'all'" />
          </el-form-item>
          <el-form-item label="优先级">
            <el-input-number v-model="fallbackDialog.form.priority" :min="0" :max="999" />
          </el-form-item>
        </div>
        <el-form-item label="匹配阈值">
          <el-slider v-model="fallbackDialog.form.threshold" :min="0" :max="100" show-input />
        </el-form-item>
        <div class="fallback-form-grid three">
          <el-form-item label="适用范围">
            <el-select v-model="fallbackDialog.form.scopeType">
              <el-option label="全局" value="global" />
              <el-option label="指定PPT" value="ppt" />
              <el-option label="指定专场" value="special" />
              <el-option label="指定屏幕" value="screen" />
            </el-select>
          </el-form-item>
          <el-form-item label="范围值">
            <el-input v-model="fallbackDialog.form.scopeValue" :disabled="fallbackDialog.form.scopeType === 'global'" :placeholder="fallbackScopePlaceholder" />
          </el-form-item>
          <el-form-item label="处理动作">
            <el-select v-model="fallbackDialog.form.actionType">
              <el-option label="直接回答" value="answer" />
              <el-option label="建议转专家" value="handoff" />
              <el-option label="拉回当前展项" value="redirect" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="状态">
          <el-switch v-model="fallbackDialog.form.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="fallbackDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="fallbackDialog.saving" @click="saveFallback">保存规则</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="webSearchDialog.visible" :title="webSearchDialog.form.id ? '编辑联网搜索规则' : '新增联网搜索规则'" width="860px" class="fallback-dialog">
      <el-form label-width="110px" class="qa-form">
        <div class="fallback-form-grid">
          <el-form-item label="规则名称">
            <el-input v-model="webSearchDialog.form.name" placeholder="例如：最新新闻与热点" />
          </el-form-item>
          <el-form-item label="分类">
            <el-select v-model="webSearchDialog.form.category" filterable allow-create default-first-option placeholder="选择或输入分类">
              <el-option v-for="category in webSearchCategories" :key="category" :label="category" :value="category" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="搜索模板">
          <el-input v-model="webSearchDialog.form.queryTemplate" placeholder="默认 {question}，可写：{question} 最新消息" />
        </el-form-item>
        <div class="fallback-form-grid">
          <el-form-item label="触发词">
            <el-input v-model="webSearchDialog.form.keywordsText" type="textarea" :rows="4" placeholder="逗号或换行分隔，例如：最新、今天、新闻、实时" />
          </el-form-item>
          <el-form-item label="排除词">
            <el-input v-model="webSearchDialog.form.excludeKeywordsText" type="textarea" :rows="4" placeholder="包含这些词时不触发，可留空" />
          </el-form-item>
        </div>
        <div class="fallback-form-grid three">
          <el-form-item label="匹配方式">
            <el-select v-model="webSearchDialog.form.matchMode">
              <el-option label="任意触发词" value="any" />
              <el-option label="全部触发词" value="all" />
            </el-select>
          </el-form-item>
          <el-form-item label="最少命中">
            <el-input-number v-model="webSearchDialog.form.minKeywordHits" :min="1" :max="20" :disabled="webSearchDialog.form.matchMode === 'all'" />
          </el-form-item>
          <el-form-item label="优先级">
            <el-input-number v-model="webSearchDialog.form.priority" :min="0" :max="999" />
          </el-form-item>
        </div>
        <el-form-item label="路由模式">
          <el-radio-group v-model="webSearchDialog.form.routeMode">
            <el-radio label="PREFER_WEB">普通联网（本地问答未命中后）</el-radio>
            <el-radio label="MUST_WEB">强制联网（时效、结果、赛程类优先）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="匹配阈值">
          <el-slider v-model="webSearchDialog.form.threshold" :min="0" :max="100" show-input />
        </el-form-item>
        <div class="fallback-form-grid three">
          <el-form-item label="时间范围">
            <el-input-number v-model="webSearchDialog.form.recencyDays" :min="0" :max="3650" />
          </el-form-item>
          <el-form-item label="结果数量">
            <el-input-number v-model="webSearchDialog.form.maxResults" :min="1" :max="10" />
          </el-form-item>
          <el-form-item label="回答前缀">
            <el-input v-model="webSearchDialog.form.answerPrefix" placeholder="根据最新公开信息" />
          </el-form-item>
        </div>
        <el-form-item label="状态">
          <el-switch v-model="webSearchDialog.form.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="webSearchDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="webSearchDialog.saving" @click="saveWebSearchRule">保存规则</el-button>
      </template>
    </el-dialog>

    <button class="floating-simulator-button" type="button" @click="simulatorPanelOpen = true">
      <span>模拟提问</span>
    </button>
    <section v-if="simulatorPanelOpen" class="floating-simulator-panel">
      <header>
        <div>
          <strong>模拟手机提问</strong>
          <span>走 ASR APK 同一条中控链路</span>
        </div>
        <button type="button" @click="simulatorPanelOpen = false">×</button>
      </header>
      <div class="floating-simulator-body">
        <el-select v-model="simulatorTarget" class="simulator-target">
          <el-option label="全部屏幕" value="all" />
          <el-option
            v-for="screen in onlineScreens"
            :key="screen.deviceId || screen.id"
            :label="`${screen.screenNumber || '-'} 号屏`"
            :value="screen.screenNumber"
          />
        </el-select>
        <el-input
          v-model="simulatorText"
          type="textarea"
          :rows="4"
          placeholder="输入一句话，例如：有哪些中国移动相关的文件？"
          clearable
          @keyup.ctrl.enter="sendSimulatorQuestion"
        />
        <el-button type="primary" :loading="simulatorSending" @click="sendSimulatorQuestion">发送</el-button>
        <div v-if="simulatorConversation" class="simulator-live-card">
          <div class="simulator-live-head">
            <span class="progress-orbit"></span>
            <strong>{{ answerProgressStageLabel(simulatorConversation.progressStage) || '处理中' }}</strong>
            <small>{{ simulatorConversation.progressMessage }}</small>
          </div>
          <div class="simulator-live-question">
            {{ simulatorConversation.visibleInput !== undefined ? simulatorConversation.visibleInput : simulatorConversation.input }}
            <i v-if="simulatorConversation.typingInput" class="typing-caret"></i>
          </div>
          <div v-if="simulatorConversation.reply" class="simulator-live-answer">
            {{ simulatorConversation.visibleReply !== undefined ? simulatorConversation.visibleReply : simulatorConversation.reply }}
            <i v-if="simulatorConversation.typingAnswer" class="typing-caret"></i>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const tabs = [
  { key: 'dashboard', label: '总览', hint: 'Overview', desc: '查看展厅在线状态、知识库和最近交互。' },
  { key: 'chat', label: '问答测试', hint: 'Chat', desc: '以会话形式测试问答、控制动作和路由诊断。' },
  { key: 'smartExhibition', label: '智能布展', hint: 'Smart', desc: '生成来访专场，并将布展结果同步到中控专场配置。' },
  { key: 'devices', label: '屏幕设备', hint: 'Screens', desc: '管理展示端在线状态、预览和基础控制。' },
  { key: 'avatar', label: '数字人管理', hint: 'Avatar', desc: '控制数字人的打开、显隐、位置和播报。' },
  { key: 'knowledge', label: '知识库', hint: 'Files / QA', desc: '管理媒体文件、讲解词和标准问答。' },
  { key: 'specials', label: '专场配置', hint: 'Scene', desc: '为不同专场配置每个屏幕的默认展示文件。' },
  { key: 'voice', label: '音色切换', hint: 'Voice', desc: '切换展示屏回答时使用的播报音色。' },
]

const voiceNames = {
  xiaoyan: '小燕',
  aisjiuxu: '许久',
  aisxping: '小萍',
  aisjinger: '小婧',
  aisbabyxu: '许小宝',
}

const activeTab = ref('dashboard')
const smartExhibitionUrl = `${window.location.protocol}//${window.location.hostname}:3001/smart-exhibition`
const avatarApiBase = `${window.location.protocol}//${window.location.hostname}:19200`
const avatarStatus = reactive({
  online: false,
  speaking: false,
  desiredOpen: false,
  session: null,
  lastCommand: null,
})
const avatarTarget = reactive({
  target: '61',
  label: '61主机',
  online: true,
  targets: [{ target: '61', label: '61主机', online: true }],
})
const avatarTargetBusy = ref(false)
const avatarBounds = reactive({ width: 3840, height: 2160, x: 0, y: 0 })
const avatarText = ref('欢迎来到思德科技展厅。')
const avatarBusy = ref('')
const avatarStatusLoading = ref(false)
const avatarLastError = ref('')
const knowledgeMode = ref('files')
const relayConnected = ref(false)
const tagHelpVisible = ref(false)
const allDevices = ref([])
const conversations = ref([])
const conversationPage = ref(1)
const conversationPageSize = 8
const selectedConversationId = ref('')
const simulatorConversationId = ref('')
const archivedConversationIds = ref(new Set())
const mediaFiles = ref([])
const specials = ref([])
const activeSpecialId = ref(null)
const selectedSpecial = ref(null)
const mediaKeyword = ref('')
const mediaType = ref('all')
const mediaPage = ref(1)
const mediaPageSize = 10
const fileUploadInput = ref(null)
const uploadingFiles = ref(false)
const qaItems = ref([])
const qaKeyword = ref('')
const qaPage = ref(1)
const qaPageSize = 12
const selectedQaIds = ref([])
const qaBatching = ref(false)
const webQaCandidates = ref([])
const webCandidatesLoading = ref(false)
const webCandidateKeyword = ref('')
const webCandidateStatus = ref('all')
const webCandidateFreshness = ref('all')
const webCandidateActionId = ref('')
const webCandidateReview = reactive({
  visible: false,
  item: null,
  question: '',
  answer: '',
  saving: false,
})
const fallbackRules = ref([])
const fallbackKeyword = ref('')
const fallbackCategory = ref('all')
const fallbackPage = ref(1)
const fallbackPageSize = 10
const fallbackTestText = ref('')
const fallbackTestTarget = ref('all')
const fallbackTesting = ref(false)
const fallbackTestResult = ref(null)
const webSearchRules = ref([])
const webSearchStatsWindow = ref(null)
const webSearchKeyword = ref('')
const webSearchCategory = ref('all')
const webSearchPage = ref(1)
const webSearchPageSize = 10
const webSearchTestText = ref('')
const webSearchTesting = ref(false)
const webSearchTestResult = ref(null)
const knowledgeStatus = ref(null)
const obsidianVaultPath = ref('')
const obsidianSyncing = ref(false)
const obsidianAnalyzing = ref(false)
const obsidianAnalyzeLimit = ref(5)
const obsidianLastImport = ref(null)
const obsidianLastAnalysis = ref(null)
const obsidianAnalyses = ref([])
const obsidianAnalysisKeyword = ref('')
const obsidianAnalysesLoading = ref(false)
const obsidianAnalysesPage = ref(1)
const obsidianAnalysesPageSize = 12
const obsidianAnalysesTotal = ref(0)
const obsidianDetailDialog = reactive({
  visible: false,
  item: null,
})
const ttsState = reactive({
  provider: 'iflytek',
  providerName: '讯飞在线语音',
  providers: {},
  voice: 'xiaoyan',
  voices: {},
  allVoices: {},
  avatarEnabled: false,
})
const ttsSaving = ref(false)
const ttsTesting = ref(false)
const avatarTtsSaving = ref(false)
const ttsPreviewText = ref('欢迎来到思德科技展厅。')
const ttsAudio = ref(null)
const localTtsStatus = reactive({ online: false, ready: false, busy: false })
const savedTts = reactive({ provider: '', voice: '', label: '' })
const savedTtsLabel = computed(() => savedTts.label || '加载中')
const ttsDirty = computed(() => savedTts.provider !== ttsState.provider || savedTts.voice !== ttsState.voice)
let ttsPreviewAbort = null
function markTtsSaved(data) {
  Object.assign(savedTts, { provider: data.provider, voice: data.voice, label: `${data.providerName} · ${data.voiceName || data.voice}` })
  Object.assign(localTtsStatus, data.localStatus || {})
}
function stopTtsPreview() {
  ttsPreviewAbort?.abort(); ttsPreviewAbort = null
  if (ttsAudio.value) { ttsAudio.value.pause(); ttsAudio.value.removeAttribute('src'); ttsAudio.value.load() }
  ttsTesting.value = false
}
watch(activeTab, () => stopTtsPreview())
async function refreshTtsCatalog() {
  try {
    const data = await fetchJson('/api/tts-settings')
    Object.assign(localTtsStatus, data.localStatus || {})
    ttsState.allVoices = data.allVoices || {}
    ttsState.voices = ttsState.allVoices[ttsState.provider] || {}
  } catch (error) { ElMessage.error(error.message) }
}

const simulatorText = ref('')
const simulatorTarget = ref('all')
const simulatorSending = ref(false)
const simulatorPanelOpen = ref(false)
const simulatorRequestId = ref('')
const commentaryDialog = reactive({
  visible: false,
  saving: false,
  file: null,
  text: '',
  level: 'brief',
  pageCount: 1,
  pages: [],
})
const commentaryLevels = [
  { key: 'brief', label: '精炼' },
  { key: 'standard', label: '标准' },
  { key: 'detail', label: '详细' },
]
const qaDialog = reactive({
  visible: false,
  saving: false,
  form: {
    id: '',
    question: '',
    answer: '',
    keywordsText: '',
    enabled: true,
    createdAt: '',
  },
})
const fallbackDialog = reactive({
  visible: false,
  saving: false,
  form: {
    id: '',
    name: '',
    category: '通用',
    answer: '',
    keywordsText: '',
    excludeKeywordsText: '',
    matchMode: 'any',
    minKeywordHits: 1,
    threshold: 60,
    priority: 100,
    scopeType: 'global',
    scopeValue: '',
    actionType: 'answer',
    enabled: true,
    createdAt: '',
  },
})
const webSearchDialog = reactive({
  visible: false,
  saving: false,
  form: {
    id: '',
    name: '',
    category: '实时',
    keywordsText: '',
    matchPatternsText: '',
    excludeKeywordsText: '',
    queryTemplate: '{question}',
    matchMode: 'any',
    minKeywordHits: 1,
    threshold: 60,
    priority: 100,
    routeMode: 'PREFER_WEB',
    recencyDays: 30,
    maxResults: 5,
    answerPrefix: '根据最新公开信息',
    enabled: true,
    createdAt: '',
  },
})
const screenSettingsDialog = reactive({
  visible: false,
  saving: false,
  deviceId: '',
  form: {
    name: '',
    snapshotIntervalSeconds: 5,
    statusIntervalSeconds: 1,
  },
})

const answerSourceTags = [
  {
    name: '唤醒应答',
    title: '固定唤醒词直接响应',
    desc: '最先检查“你好思德”等固定唤醒表达，命中后直接回复并播报，不进入联网、知识库或大模型问答。',
  },
  {
    name: '控制',
    title: '确定性中控操作',
    desc: '前置识别模型通道切换、翻页、返回、黑屏、专场切换等控制意图，命中后直接下发受控动作。',
  },
  {
    name: '控制忽略',
    title: '手势或控制未执行',
    desc: 'APK 发来了控制手势，但当前屏幕模式、页面状态或目标对象不满足执行条件，系统安全忽略。',
  },
  {
    name: 'QA',
    title: '人工维护的标准答案',
    desc: '人工提升的联网候选会在强制联网前做精确匹配；常规 QA 则在前置功能和优先联网未命中后匹配，均直接返回已审核答案。',
  },
  {
    name: '联网搜索',
    title: 'Tavily 公开资料回答',
    desc: '强制联网问题会在日期时间和本地问答前执行；优先联网或普通联网问题按各自阶段执行。系统筛选网页来源后使用 WebSearch 专用 Prompt 组织回答。',
  },
  {
    name: '联网搜索异常',
    title: '联网链路执行失败',
    desc: '问题已命中联网规则，但 Tavily、网页结果或回答生成没有成功完成，系统返回统一的工作人员提示，不回落到旧知识直接猜答。',
  },
  {
    name: '日期时间',
    title: '服务器时钟确定性回答',
    desc: '命中当前时间、日期、星期或农历问题后，直接按 Asia/Shanghai 读取服务器时间，不调用 Tavily 或大模型。',
  },
  {
    name: '天气缓存',
    title: '已有天气结果',
    desc: '日期时间之后检查天气意图；若本地缓存仍有效，直接返回缓存结果，不重复调用高德接口。',
  },
  {
    name: '天气服务',
    title: '高德天气实时查询',
    desc: '命中天气问题且缓存不可用或已过期时，调用高德天气接口获取当前结果。',
  },
  {
    name: '天气兜底',
    title: '天气获取失败',
    desc: '天气意图已命中，但接口失败、城市无法识别或没有可用缓存时返回固定失败提示。',
  },
  {
    name: '讲解词规则',
    title: '当前 PPT 固定讲解词',
    desc: '命中当前页精炼版、标准版或详细版讲解指令后，读取已维护的 PPT 讲解词并直接播报。',
  },
  {
    name: '实时生成讲解词',
    title: '当前 PPT 个性化讲解',
    desc: '当前页讲解需要结合来访企业或行业画像时，使用当前 PPT 详细讲解词生成现场播报文本。',
  },
  {
    name: '详细版讲解词兜底',
    title: '实时讲解生成失败回退',
    desc: '个性化讲解没有生成有效文本时，使用当前页已有的详细版讲解词继续播报。',
  },
  {
    name: '专场文件',
    title: '当前专场屏幕文件',
    desc: '命中当前专场的屏幕文件列表请求后，按目标屏展示该专场已经配置的媒体文件。',
  },
  {
    name: '文件打开',
    title: '执行打开媒体文件',
    desc: '用户明确选择或指定 PPT、视频、图片后，系统向目标屏下发对应的打开或播放动作。',
  },
  {
    name: 'ChromaDB 文件',
    title: '向量语义文件搜索',
    desc: '文件请求无法直接按名称确定时，通过 ChromaDB 语义检索相关 PPT、视频或图片。',
  },
  {
    name: '文件检索',
    title: '本地媒体库文件搜索',
    desc: '通过文件名、标题、标签等本地媒体信息找到可用 PPT、视频或图片，并返回文件列表。',
  },
  {
    name: 'ChromaDB QA',
    title: '向量 QA 高置信命中',
    desc: '进入常规本地问答阶段后，ChromaDB 找到高置信度的已有 QA，系统直接返回该 QA 保存的答案，不重新生成。',
  },
  {
    name: 'ChromaDB QA+AI',
    title: '向量候选经 AI 确认',
    desc: 'ChromaDB 只找到相似候选时，由 FAQ 匹配模型判断是否同义；确认后仍返回候选 QA 中保存的原答案。',
  },
  {
    name: 'QA+AI',
    title: '本地 QA 候选经 AI 确认',
    desc: '本地 Markdown/索引产生的 QA 候选未达到直接命中阈值时，由 FAQ 匹配模型确认语义后返回原答案。',
  },
  {
    name: '兜底库',
    title: '可配置业务兜底话术',
    desc: '常规 QA 未命中后，按照适用范围、优先级、触发词和匹配阈值返回后台维护的业务话术。',
  },
  {
    name: '本地知识库',
    title: '明确实体缺少对应资料',
    desc: '问题包含明确机构、项目或产品，但本地 RAG 没有找到同一实体的资料时，系统停止继续猜答并返回能力范围提示。',
  },
  {
    name: 'ChromaDB RAG',
    title: '向量知识片段生成回答',
    desc: '前面的 QA、兜底和联网均未返回时，从 ChromaDB 检索 Markdown/知识对象片段，再使用 rag-answer-v1 Prompt 基于片段回答。',
  },
  {
    name: 'RAG',
    title: '本地资料检索回答',
    desc: 'ChromaDB 没有提供片段时，使用本地 Markdown 索引召回资料，再使用同一套 rag-answer-v1 Prompt 回答。',
  },
  {
    name: 'LLM',
    title: '大模型直接回答',
    desc: '所有前置控制、联网、日期时间、天气、媒体、QA、兜底和 RAG 均未命中时，调用当前模型处理通用常识、解释或写作问题。',
  },
  {
    name: '规则兜底',
    title: '最终固定规则回退',
    desc: '普通大模型调用失败、返回空结果或触发已有内置安全兜底时，使用固定短答案保证请求有唯一终态。',
  },
]

const config = reactive({
  relayUrl: defaultRelayUrl(),
  fileServerUrl: defaultFileServerUrl(),
})

let ws = null
let heartbeatTimer = null
let refreshTimer = null
const conversationTypingTimers = new Map()

const currentTab = computed(() => tabs.find(item => item.key === activeTab.value))
const onlineScreens = computed(() => allDevices.value.filter(item => item.type === 'client' && item.online))
const screenSummaryText = computed(() => {
  const numbers = onlineScreens.value
    .map(item => Number(item.screenNumber))
    .filter(Number.isFinite)
    .sort((a, b) => a - b)
  return numbers.length ? `${numbers.join('、')} 号屏在线` : '暂无展示屏幕在线'
})
const sortedDevices = computed(() => [...allDevices.value].sort((a, b) => (Number(a.screenNumber) || 9999) - (Number(b.screenNumber) || 9999)))
const voiceDisplayName = computed(() => ttsState.voices[ttsState.voice] || '')
const enabledQaCount = computed(() => qaItems.value.filter(item => item.enabled !== false).length)
const pendingWebCandidateCount = computed(() =>
  webQaCandidates.value.filter(item => item.status === 'CANDIDATE').length
)
const eligibleWebCandidateCount = computed(() =>
  webQaCandidates.value.filter(item => canPromoteWebCandidate(item)).length
)
const promotedWebCandidateCount = computed(() =>
  webQaCandidates.value.filter(item => item.status === 'PROMOTED').length
)
const timeSensitiveWebCandidateCount = computed(() =>
  webQaCandidates.value.filter(item =>
    item.status === 'CANDIDATE'
    && ['TIME_BOUND', 'TIME_SENSITIVE'].includes(item.freshnessClass)
  ).length
)
const filteredWebQaCandidates = computed(() => {
  const keyword = webCandidateKeyword.value.trim().toLowerCase()
  return webQaCandidates.value.filter(item => {
    if (webCandidateStatus.value !== 'all' && item.status !== webCandidateStatus.value) return false
    if (webCandidateFreshness.value !== 'all' && item.freshnessClass !== webCandidateFreshness.value) return false
    if (!keyword) return true
    const sourceText = (item.sources || [])
      .map(source => `${source.title || ''} ${source.domain || ''} ${source.url || ''}`)
      .join(' ')
    return `${item.question || ''} ${item.answer || ''} ${sourceText}`.toLowerCase().includes(keyword)
  })
})
const pagedConversations = computed(() => {
  const start = (conversationPage.value - 1) * conversationPageSize
  return conversations.value.slice(start, start + conversationPageSize)
})
const conversationSessions = computed(() => {
  const groups = new Map()
  for (const item of conversations.value) {
    const id = item.conversationId || `legacy-${item.id}`
    if (!groups.has(id)) groups.set(id, { id, items: [], firstAt: Infinity, lastAt: 0 })
    const group = groups.get(id)
    const timestamp = new Date(item.time || Date.now()).getTime()
    group.items.push(item)
    group.firstAt = Math.min(group.firstAt, timestamp)
    group.lastAt = Math.max(group.lastAt, timestamp)
  }
  return [...groups.values()]
    .map(group => ({
      ...group,
      items: [...group.items].sort((a, b) => new Date(a.time) - new Date(b.time)),
      title: group.items.find(item => item.conversationTitle)?.conversationTitle
        || group.items.at(-1)?.input
        || group.items[0]?.input
        || '新会话',
      archived: archivedConversationIds.value.has(group.id) || Date.now() - group.lastAt >= 3 * 60 * 1000,
    }))
    .sort((a, b) => b.lastAt - a.lastAt)
})
const selectedConversationSession = computed(() =>
  conversationSessions.value.find(item => item.id === selectedConversationId.value) || null
)
const selectedConversationItems = computed(() => selectedConversationSession.value?.items || [])
const selectedDiagnosticTurn = computed(() => selectedConversationItems.value.at(-1) || null)
const simulatorConversation = computed(() =>
  simulatorRequestId.value
    ? conversations.value.find(item => item.requestId === simulatorRequestId.value) || null
    : null
)
const deviceSummary = computed(() => ({
  total: allDevices.value.length,
  online: allDevices.value.filter(item => item.online).length,
  client: allDevices.value.filter(item => item.type === 'client').length,
}))
const filteredMediaFiles = computed(() => {
  const keyword = mediaKeyword.value.trim().toLowerCase()
  return mediaFiles.value.filter(item => {
    const typeOk = mediaType.value === 'all' || item.type === mediaType.value
    const text = `${item.title || ''} ${item.name || ''} ${item.originalName || ''}`.toLowerCase()
    return typeOk && (!keyword || text.includes(keyword))
  })
})

const activeCommentaryLevelLabel = computed(() =>
  commentaryLevels.find(level => level.key === commentaryDialog.level)?.label || '精炼'
)
const pagedMediaFiles = computed(() => {
  const start = (mediaPage.value - 1) * mediaPageSize
  return filteredMediaFiles.value.slice(start, start + mediaPageSize)
})
const filteredQaItems = computed(() => {
  const keyword = qaKeyword.value.trim().toLowerCase()
  if (!keyword) return qaItems.value
  return qaItems.value.filter(item => {
    const text = `${item.question || ''} ${item.answer || ''} ${(item.keywords || []).join(' ')}`.toLowerCase()
    return text.includes(keyword)
  })
})
const pagedQaItems = computed(() => {
  const start = (qaPage.value - 1) * qaPageSize
  return filteredQaItems.value.slice(start, start + qaPageSize)
})
const selectedQaIdSet = computed(() => new Set(selectedQaIds.value))
const qaPageSelectionState = computed(() => {
  const pageIds = pagedQaItems.value.map(item => item.id).filter(Boolean)
  const selected = pageIds.filter(id => selectedQaIdSet.value.has(id)).length
  return {
    checked: pageIds.length > 0 && selected === pageIds.length,
    indeterminate: selected > 0 && selected < pageIds.length,
  }
})
const fallbackCategories = computed(() => [...new Set(fallbackRules.value.map(item => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN')))
const filteredFallbackRules = computed(() => {
  const keyword = fallbackKeyword.value.trim().toLowerCase()
  return fallbackRules.value.filter(item => {
    if (fallbackCategory.value !== 'all' && item.category !== fallbackCategory.value) return false
    if (!keyword) return true
    const text = `${item.name || ''} ${item.category || ''} ${item.answer || ''} ${(item.keywords || []).join(' ')} ${(item.excludeKeywords || []).join(' ')}`.toLowerCase()
    return text.includes(keyword)
  })
})
const pagedFallbackRules = computed(() => {
  const start = (fallbackPage.value - 1) * fallbackPageSize
  return filteredFallbackRules.value.slice(start, start + fallbackPageSize)
})
const enabledFallbackCount = computed(() => fallbackRules.value.filter(item => item.enabled !== false).length)
const fallbackScopePlaceholder = computed(() => ({
  global: '全局规则无需填写',
  ppt: '输入PPT文件名或标题关键词',
  special: '输入专场名称关键词',
  screen: '输入屏幕编号，例如 2',
}[fallbackDialog.form.scopeType] || ''))
const webSearchCategories = computed(() => [...new Set(webSearchRules.value.map(item => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN')))
const filteredWebSearchRules = computed(() => {
  const keyword = webSearchKeyword.value.trim().toLowerCase()
  return webSearchRules.value.filter(item => {
    if (webSearchCategory.value !== 'all' && item.category !== webSearchCategory.value) return false
    if (!keyword) return true
    const text = `${item.name || ''} ${item.category || ''} ${item.queryTemplate || ''} ${(item.keywords || []).join(' ')} ${(item.excludeKeywords || []).join(' ')}`.toLowerCase()
    return text.includes(keyword)
  })
})
const pagedWebSearchRules = computed(() => {
  const start = (webSearchPage.value - 1) * webSearchPageSize
  return filteredWebSearchRules.value.slice(start, start + webSearchPageSize)
})
const enabledWebSearchCount = computed(() => webSearchRules.value.filter(item => item.enabled !== false).length)
const voiceOptions = computed(() => Object.keys(ttsState.voices).map(key => ({
  key,
  name: voiceNames[key] || ttsState.voices[key] || key,
})))
const ttsProviderOptions = computed(() => Object.keys(ttsState.providers).map(key => ({
  key,
  name: ttsState.providers[key] || key,
})))
const knowledgeCountCards = computed(() => {
  const counts = knowledgeStatus.value?.counts || {}
  return [
    { key: 'resources', label: '文件资源', value: counts.resources ?? 0 },
    { key: 'knowledge_objects', label: 'Markdown对象', value: counts.knowledge_objects ?? 0 },
    { key: 'qa_items', label: 'QA问答', value: counts.qa_items ?? 0 },
    { key: 'scenes', label: '专场', value: counts.scenes ?? 0 },
    { key: 'missed_questions', label: '未命中问题', value: counts.missed_questions ?? 0 },
  ]
})
const isPptCommentaryFile = computed(() => commentaryDialog.file?.type === 'ppt')
const screenAssignments = computed(() => {
  if (!selectedSpecial.value) return []
  selectedSpecial.value.assignments ||= []
  return [...selectedSpecial.value.assignments]
    .sort((a, b) => (Number(a.screenNumber) || 9999) - (Number(b.screenNumber) || 9999))
})

watch(filteredMediaFiles, files => {
  const maxPage = Math.max(1, Math.ceil(files.length / mediaPageSize))
  if (mediaPage.value > maxPage) mediaPage.value = maxPage
})

watch(conversations, items => {
  const maxPage = Math.max(1, Math.ceil(items.length / conversationPageSize))
  if (conversationPage.value > maxPage) conversationPage.value = maxPage
})

watch(conversationSessions, sessions => {
  if (!selectedConversationId.value && sessions.length) selectedConversationId.value = sessions[0].id
  if (selectedConversationId.value && !sessions.some(item => item.id === selectedConversationId.value)) {
    selectedConversationId.value = sessions[0]?.id || ''
  }
})

watch(filteredQaItems, items => {
  const maxPage = Math.max(1, Math.ceil(items.length / qaPageSize))
  if (qaPage.value > maxPage) qaPage.value = maxPage
  const validIds = new Set(items.map(item => item.id))
  selectedQaIds.value = selectedQaIds.value.filter(id => validIds.has(id))
})

watch(qaKeyword, () => {
  resetQaPage()
})

function defaultRelayUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.hostname || '127.0.0.1'}:3000`
}

function defaultFileServerUrl() {
  return `${window.location.protocol}//${window.location.hostname || '127.0.0.1'}:3001`
}

function connectWS() {
  ws = new WebSocket(config.relayUrl)
  ws.onopen = () => {
    relayConnected.value = true
    ws.send(JSON.stringify({ type: 'register', deviceType: 'admin', name: '管理后台', os: navigator.platform, version: '1.0.0' }))
    clearInterval(heartbeatTimer)
    heartbeatTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'heartbeat' }))
    }, 15000)
  }
  ws.onmessage = event => {
    let msg
    try { msg = JSON.parse(event.data) } catch { return }
    if (msg.type === 'device_event') refreshDevices()
    if (msg.type === 'answer_progress') handleAnswerProgress(msg)
    if (msg.type === 'conversation_session_archived' && msg.session?.id) {
      archivedConversationIds.value = new Set([...archivedConversationIds.value, msg.session.id])
      if (simulatorConversationId.value === msg.session.id) simulatorConversationId.value = ''
    }
    if (msg.type === 'conversation_log') {
      const conversation = upsertConversation({
        id: msg.eventId || `${msg.time || Date.now()}-${msg.seq || ''}-${msg.deviceId || ''}`,
        requestId: msg.requestId || '',
        conversationId: msg.conversationId || '',
        seq: msg.seq,
        deviceId: msg.deviceId,
        deviceType: msg.deviceType,
        source: msg.source,
        target: msg.target || 'all',
        input: msg.input || '(语音消息)',
        reply: msg.reply,
        actions: msg.actions,
        answerSource: msg.answerSource || sourceLabelFromActions(msg.actions),
        finalRoute: msg.finalRoute || '',
        routeTrace: msg.routeTrace || [],
        webSources: msg.webSources || [],
        knowledgeSources: msg.knowledgeSources || [],
        webSearchStatus: msg.webSearchStatus || '',
        time: msg.time || Date.now(),
        answerTime: msg.time || Date.now(),
        pending: false,
        progressStage: 'COMPLETED',
        progressMessage: '回答完成',
      })
      if (msg.deviceType === 'admin' && msg.conversationId) {
        simulatorConversationId.value = msg.conversationId
        selectedConversationId.value = msg.conversationId
      }
      animateConversationField(conversation.id, 'visibleReply', msg.reply || '', 24)
    }
  }
  ws.onclose = () => {
    relayConnected.value = false
    clearInterval(heartbeatTimer)
    setTimeout(connectWS, 3000)
  }
  ws.onerror = () => {
    relayConnected.value = false
  }
}

function sourceLabelFromActions(actions = []) {
  if (!Array.isArray(actions) || actions.length === 0) return ''
  if (actions.some(action => action?.type === 'show_media_results')) return '文件检索'
  if (actions.some(action => ['play_ppt', 'play_video', 'show_doc'].includes(action?.type))) return '文件打开'
  if (actions.some(action => action?.type === 'show_qa')) return 'LLM'
  return '控制'
}

function upsertConversation(item) {
  const index = conversations.value.findIndex(existing =>
    (item.id && existing.id === item.id)
    || (item.requestId && existing.requestId === item.requestId)
    || (item.seq !== undefined && item.seq !== null && existing.seq === item.seq && (existing.pending || item.pending))
  )
  let saved
  if (index >= 0) {
    saved = {
      ...conversations.value[index],
      ...item,
      id: conversations.value[index].id,
      eventId: item.eventId || item.id || conversations.value[index].eventId,
    }
    conversations.value[index] = saved
  } else {
    saved = item
    conversations.value.unshift(saved)
  }
  conversations.value = conversations.value.slice(0, 200)
  return saved
}

function answerProgressStageLabel(stage) {
  return {
    RECEIVED: '已接收',
    ROUTING: '识别中',
    SEARCHING: '搜索中',
    SOURCES_FOUND: '来源整理',
    SYNTHESIZING: '整合中',
    STREAMING: '输出中',
    COMPLETED: '已完成',
    FAILED: '处理失败',
    CANCELLED: '已取消',
  }[stage] || stage || ''
}

function handleAnswerProgress(msg) {
  const id = msg.requestId ? `request-${msg.requestId}` : `progress-${msg.seq}`
  const existing = conversations.value.find(item =>
    item.id === id
    || (msg.requestId && item.requestId === msg.requestId)
    || (item.seq === msg.seq && item.pending)
  )
  const conversation = upsertConversation({
    id: existing?.id || id,
    requestId: msg.requestId || existing?.requestId || '',
    conversationId: msg.conversationId || existing?.conversationId || simulatorConversationId.value || '',
    seq: msg.seq,
    deviceId: existing?.deviceId || msg.source || '处理中',
    deviceType: existing?.deviceType || '',
    source: msg.source || existing?.source || '',
    input: msg.question || existing?.input || '',
    reply: msg.answer || existing?.reply || '',
    visibleInput: existing?.visibleInput,
    visibleReply: existing?.visibleReply,
    progressStage: msg.stage || existing?.progressStage || 'RECEIVED',
    progressMessage: msg.message || existing?.progressMessage || '正在处理…',
    questionTime: existing?.questionTime || Date.now(),
    pending: msg.terminal !== true,
    time: existing?.time || msg.startedAt || msg.serverTime || Date.now(),
  })

  if (!existing && conversation.input) {
    conversation.visibleInput = ''
    animateConversationField(conversation.id, 'visibleInput', conversation.input, 18)
  }
  if (msg.answer && ['STREAMING', 'COMPLETED'].includes(msg.stage)) {
    animateConversationField(conversation.id, 'visibleReply', msg.answer, 24)
  }
}

function animateConversationField(id, field, targetValue, intervalMs) {
  const target = String(targetValue || '')
  const timerKey = `${id}:${field}`
  const currentTimer = conversationTypingTimers.get(timerKey)
  if (currentTimer) clearInterval(currentTimer)

  const index = conversations.value.findIndex(item => item.id === id)
  if (index < 0) return
  const typingField = field === 'visibleInput' ? 'typingInput' : 'typingAnswer'
  const currentItem = conversations.value[index]
  const currentValue = String(currentItem[field] || '')
  if (!target || currentValue === target) {
    conversations.value[index] = {
      ...currentItem,
      [field]: target || undefined,
      [typingField]: false,
    }
    return
  }

  let offset = target.startsWith(currentValue) ? currentValue.length : 0
  const step = target.length > 180 ? 4 : target.length > 90 ? 2 : 1
  conversations.value[index] = {
    ...currentItem,
    [field]: target.slice(0, offset),
    [typingField]: true,
  }
  const timer = setInterval(() => {
    const itemIndex = conversations.value.findIndex(item => item.id === id)
    if (itemIndex < 0) {
      clearInterval(timer)
      conversationTypingTimers.delete(timerKey)
      return
    }
    offset = Math.min(target.length, offset + step)
    const done = offset >= target.length
    conversations.value[itemIndex] = {
      ...conversations.value[itemIndex],
      [field]: done ? undefined : target.slice(0, offset),
      [typingField]: !done,
    }
    if (done) {
      clearInterval(timer)
      conversationTypingTimers.delete(timerKey)
    }
  }, intervalMs)
  conversationTypingTimers.set(timerKey, timer)
}

function conversationDeviceLabel(item) {
  if (item.deviceType === 'admin' || item.source === 'admin-simulator') return '管理后台'
  const device = allDevices.value.find(row => row.deviceId === item.deviceId || row.id === item.deviceId)
  if (device) {
    const name = device.settings?.name || device.name
    if (name) return name
    if (device.screenNumber) return `${device.screenNumber}号屏`
  }
  if (item.deviceType === 'mobile') return '移动端'
  const raw = item.deviceId || item.source || ''
  if (/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(raw)) return '已注册设备'
  return raw || '未知设备'
}

async function refreshAll() {
  await Promise.all([refreshDevices(), refreshMedia(), refreshSpecials(), refreshTtsSettings(), refreshQa(), refreshWebQaCandidates(), refreshFallbackRules(), refreshWebSearchRules(), refreshKnowledgeStatus(), refreshObsidianAnalyses(), refreshConversations(), refreshAvatarStatus(true), refreshAvatarTarget(true)])
}

async function avatarRequest(path, body) {
  const response = await fetch(`${avatarApiBase}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok || data.ok === false) throw new Error(data.error || `数字人服务请求失败 (${response.status})`)
  return data
}

async function refreshAvatarStatus(silent = true) {
  if (!silent) avatarStatusLoading.value = true
  try {
    const data = await avatarRequest('/health')
    Object.assign(avatarStatus, data)
    if (data.bounds) Object.assign(avatarBounds, data.bounds)
    avatarLastError.value = ''
  } catch (error) {
    avatarStatus.online = false
    avatarStatus.session = null
    avatarLastError.value = error.message || '无法连接数字人控制服务'
    if (!silent) ElMessage.error(avatarLastError.value)
  } finally {
    avatarStatusLoading.value = false
  }
}

async function runAvatarAction(key, path, body, successMessage) {
  avatarBusy.value = key
  try {
    await avatarRequest(path, body)
    ElMessage.success(successMessage)
    await refreshAvatarStatus(true)
  } catch (error) {
    avatarLastError.value = error.message || '数字人操作失败'
    ElMessage.error(avatarLastError.value)
  } finally {
    avatarBusy.value = ''
  }
}

function openAvatar() {
  return runAvatarAction('open', '/api/avatar/open', {}, '正在打开数字人')
}

function exitAvatar() {
  return runAvatarAction('exit', '/api/avatar/exit', {}, '正在退出数字人')
}

function showAvatar() {
  return runAvatarAction('show', '/api/avatar/show', { duration: 300 }, '数字人画面已显示')
}

function hideAvatar() {
  return runAvatarAction('hide', '/api/avatar/hide', { duration: 300 }, '数字人画面已隐藏')
}

function resetAvatar() {
  Object.assign(avatarBounds, { width: 3840, height: 2160, x: 0, y: 0 })
  return runAvatarAction('reset', '/api/avatar/reset', { ...avatarBounds }, '数字人已全屏归位')
}

function applyAvatarBounds() {
  return runAvatarAction('bounds', '/api/avatar/set-bounds', { ...avatarBounds }, '数字人窗口位置已更新')
}

function sayAvatar() {
  const text = avatarText.value.trim()
  if (!text) {
    ElMessage.warning('请先输入播报内容')
    return
  }
  if (savedTts.provider === 'qwen3') return fetchJson('/api/tts-live', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) }).then(data => { if (!data.ok) throw new Error(data.error); ElMessage.success('已发送至展示屏，使用已保存音色') }).catch(error => ElMessage.error(error.message))
  return runAvatarAction('say', '/api/avatar/say', { text }, '数字人播报指令已发送')
}

function interruptAvatar() {
  if (savedTts.provider === 'qwen3') return fetchJson('/api/tts-live', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'stop' }) }).catch(error => ElMessage.error(error.message))
  return runAvatarAction('interrupt', '/api/avatar/interrupt', {}, '已打断数字人播报')
}

function formatAvatarTime(value) {
  if (!value) return '暂无执行记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}

async function refreshConversations() {
  try {
    const data = await fetchJson('/api/usage-events?limit=80')
    conversations.value = (data.events || []).map(item => ({
      id: item.id || `${item.time}-${item.seq || ''}`,
      seq: item.seq,
      deviceId: item.deviceId || item.source || '未知来源',
      deviceType: item.deviceType || '',
      source: item.source || '',
      target: item.target || 'all',
      conversationId: item.conversationId || '',
      conversationTitle: item.conversationTitle || '',
      input: item.question || '',
      reply: item.answer || item.error || '',
      actions: item.actions || [],
      answerSource: item.answerSource || sourceLabelFromActions(item.actions),
      finalRoute: item.finalRoute || '',
      routeTrace: item.routeTrace || [],
      webSources: item.webSources || [],
      knowledgeSources: item.knowledgeSources || [],
      webSearchStatus: item.webSearchStatus || '',
      time: item.time || Date.now(),
      questionTime: item.questionTime || item.time || Date.now(),
      answerTime: item.answerTime || item.time || Date.now(),
    }))
  } catch {
    // 历史记录拉取失败不影响主控制台使用。
  }
}

async function refreshDevices() {
  try {
    const data = await fetchJson('/api/screens')
    const ids = new Set()
    for (const screen of data.screens || []) {
      ids.add(screen.deviceId)
      upsertDevice({ ...screen, id: screen.deviceId, type: 'client', online: screen.online === true })
    }
    allDevices.value = allDevices.value.map(item =>
      item.type === 'client' && !ids.has(item.deviceId) ? { ...item, online: false } : item
    )
  } catch {
    ElMessage.warning('屏幕状态刷新失败')
  }
}

async function refreshMedia() {
  const data = await fetchJson('/api/files')
  mediaFiles.value = (data.files || [])
    .filter(item => ['ppt', 'video', 'image'].includes(item.type))
    .map(item => ({ ...item, hasCommentary: hasAnyCommentary(item) }))
}

function resetMediaPage() {
  mediaPage.value = 1
}

function resetQaPage() {
  qaPage.value = 1
}

function resetFallbackPage() {
  fallbackPage.value = 1
}

function resetWebSearchPage() {
  webSearchPage.value = 1
}

function mediaRowNumber(index) {
  return (mediaPage.value - 1) * mediaPageSize + index + 1
}

function triggerFileUpload() {
  fileUploadInput.value?.click()
}

async function handleUploadFiles(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length) return

  uploadingFiles.value = true
  try {
    let done = 0
    for (const file of files) {
      const name = encodeURIComponent(file.name)
      await fetchJson(`/api/files/upload?name=${name}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-File-Name': name,
        },
        body: file,
      })
      done += 1
    }
    await refreshMedia()
    ElMessage.success(`已上传 ${done} 个文件`)
  } catch (err) {
    ElMessage.error(`上传失败：${err.message || err}`)
  } finally {
    uploadingFiles.value = false
  }
}

async function refreshQa() {
  const data = await fetchJson('/api/qa')
  qaItems.value = [...(data.items || [])].sort((left, right) =>
    String(right.updatedAt || right.createdAt || '').localeCompare(String(left.updatedAt || left.createdAt || ''))
  )
}

async function refreshWebQaCandidates() {
  webCandidatesLoading.value = true
  try {
    const data = await fetchJson('/api/web-qa-candidates')
    webQaCandidates.value = data.items || []
  } catch (error) {
    ElMessage.error(`联网候选刷新失败：${error.message || error}`)
  } finally {
    webCandidatesLoading.value = false
  }
}

async function refreshFallbackRules() {
  const data = await fetchJson('/api/fallback-rules')
  fallbackRules.value = data.items || []
}

async function refreshWebSearchRules() {
  const data = await fetchJson('/api/web-search/rules')
  webSearchRules.value = data.items || []
  webSearchStatsWindow.value = data.statsWindow || null
}

async function refreshKnowledgeStatus() {
  const data = await fetchJson('/api/knowledge/status')
  knowledgeStatus.value = data.status || null
  if (!obsidianVaultPath.value) obsidianVaultPath.value = knowledgeStatus.value?.obsidianVaultPath || ''
}

async function refreshObsidianAnalyses() {
  obsidianAnalysesLoading.value = true
  try {
    const page = Math.max(1, Number(obsidianAnalysesPage.value) || 1)
    const params = new URLSearchParams({
      q: obsidianAnalysisKeyword.value.trim(),
      limit: String(obsidianAnalysesPageSize),
      offset: String((page - 1) * obsidianAnalysesPageSize),
    })
    const data = await fetchJson(`/api/knowledge/obsidian-analyses?${params}`)
    obsidianAnalyses.value = data.items || []
    obsidianAnalysesTotal.value = Number(data.total) || obsidianAnalyses.value.length
  } catch (err) {
    ElMessage.warning(`Obsidian分析结果读取失败：${err.message || err}`)
  } finally {
    obsidianAnalysesLoading.value = false
  }
}

async function searchObsidianAnalyses() {
  obsidianAnalysesPage.value = 1
  await refreshObsidianAnalyses()
}

function openObsidianAnalysisDetail(item) {
  obsidianDetailDialog.item = item
  obsidianDetailDialog.visible = true
}

async function syncObsidian() {
  obsidianSyncing.value = true
  try {
    const data = await fetchJson('/api/knowledge/obsidian-sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vaultPath: obsidianVaultPath.value.trim() }),
    })
    knowledgeStatus.value = data.status || null
    obsidianLastImport.value = knowledgeStatus.value?.obsidianImported || null
    if (knowledgeStatus.value?.obsidianVaultPath) obsidianVaultPath.value = knowledgeStatus.value.obsidianVaultPath
    await Promise.all([refreshQa(), refreshSpecials(), refreshObsidianAnalyses()])
    ElMessage.success('Obsidian 同步完成')
  } catch (err) {
    ElMessage.error(`Obsidian 同步失败：${err.message || err}`)
  } finally {
    obsidianSyncing.value = false
  }
}

async function analyzeObsidian() {
  obsidianAnalyzing.value = true
  try {
    const data = await fetchJson('/api/knowledge/obsidian-analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        vaultPath: obsidianVaultPath.value.trim(),
        limit: obsidianAnalyzeLimit.value,
      }),
    })
    knowledgeStatus.value = data.status || null
    obsidianLastImport.value = knowledgeStatus.value?.obsidianImported || null
    obsidianLastAnalysis.value = knowledgeStatus.value?.analysis || null
    if (knowledgeStatus.value?.obsidianVaultPath) obsidianVaultPath.value = knowledgeStatus.value.obsidianVaultPath
    await Promise.all([refreshQa(), refreshSpecials(), refreshObsidianAnalyses()])
    ElMessage.success(`AI分析完成：${obsidianLastAnalysis.value?.success || 0} 个成功`)
  } catch (err) {
    ElMessage.error(`AI分析失败：${err.message || err}`)
  } finally {
    obsidianAnalyzing.value = false
  }
}

async function refreshSpecials() {
  const data = await fetchJson('/api/specials')
  specials.value = data.specials || []
  activeSpecialId.value = data.activeId || null
  if (selectedSpecial.value) {
    selectedSpecial.value = cloneSpecial(specials.value.find(item => item.id === selectedSpecial.value.id) || selectedSpecial.value)
  }
}

async function refreshTtsSettings() {
  const data = await fetchJson('/api/tts-settings')
  markTtsSaved(data)
  ttsState.provider = data.provider || 'iflytek'
  ttsState.providerName = data.providerName || ''
  ttsState.providers = data.providers || {}
  ttsState.voice = data.voice || 'xiaoyan'
  ttsState.voices = data.voices || {}
  ttsState.allVoices = data.allVoices || {}
  ttsState.avatarEnabled = data.avatarEnabled === true
}

function selectTtsProvider(provider) {
  if (ttsState.provider === provider) return
  ttsState.provider = provider
  ttsState.providerName = ttsState.providers[provider] || provider
  ttsState.voices = ttsState.allVoices[provider] || {}
  const availableVoices = Object.keys(ttsState.voices)
  if (!availableVoices.includes(ttsState.voice)) {
    ttsState.voice = availableVoices[0] || ''
  }
}

async function fetchJson(path, options = {}) {
  const res = await fetch(`${config.fileServerUrl.replace(/\/+$/, '')}${path}`, { cache: 'no-store', ...options })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

function upsertDevice(device) {
  const index = allDevices.value.findIndex(item => item.deviceId === device.deviceId || item.id === device.id)
  if (index >= 0) allDevices.value[index] = { ...allDevices.value[index], ...device }
  else allDevices.value.push(device)
}

function createSpecial() {
  selectedSpecial.value = { id: '', name: `新专场${specials.value.length + 1}`, description: '', assignments: [] }
  ensureSelectedSpecialAssignments()
  activeTab.value = 'specials'
}

function selectSpecial(item) {
  selectedSpecial.value = cloneSpecial(item)
  ensureSelectedSpecialAssignments()
}

function cloneSpecial(item) {
  const cloned = JSON.parse(JSON.stringify(item))
  cloned.assignments = (cloned.assignments || []).map(normalizeAssignmentFiles)
  return cloned
}

function ensureSelectedSpecialAssignments() {
  if (!selectedSpecial.value) return
  selectedSpecial.value.assignments ||= []
  selectedSpecial.value.assignments.forEach(normalizeAssignmentFiles)
  const byScreen = new Map(selectedSpecial.value.assignments.map(item => [Number(item.screenNumber), item]))
  const screens = onlineScreens.value.length ? onlineScreens.value : [{ screenNumber: 1 }, { screenNumber: 2 }, { screenNumber: 3 }]
  for (const screen of screens) {
    const screenNumber = Number(screen.screenNumber)
    if (!Number.isFinite(screenNumber) || byScreen.has(screenNumber)) continue
    const next = { screenNumber, fileName: '', fileNames: [] }
    selectedSpecial.value.assignments.push(next)
    byScreen.set(screenNumber, next)
  }
}

async function saveSpecial() {
  if (!selectedSpecial.value) return
  const assignments = (selectedSpecial.value.assignments || [])
    .map(normalizeAssignmentFiles)
    .filter(item => item.action || item.fileNames.length)
  const payload = {
    ...selectedSpecial.value,
    assignments,
  }
  const data = await fetchJson('/api/specials', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  selectedSpecial.value = cloneSpecial(data.special)
  await refreshSpecials()
  ElMessage.success('专场配置已保存')
}

async function activateSpecial() {
  if (!selectedSpecial.value) return
  if (!selectedSpecial.value.id) await saveSpecial()
  const id = selectedSpecial.value.id
  const data = await fetchJson('/api/specials/activate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  })
  activeSpecialId.value = data.special?.id || id
  ElMessage.success(`已切换专场，投放 ${data.applied?.length || 0} 个屏幕`)
}

function normalizeAssignmentFiles(item) {
  const source = Array.isArray(item.fileNames) ? item.fileNames : [item.fileName]
  const values = source
    .map(value => String(value || '').trim())
    .filter(Boolean)
  const normalized = [...new Set(values)]
  item.fileNames = normalized
  item.fileName = normalized[0] || ''
  return item
}

function assignmentFileCount(item) {
  const source = Array.isArray(item.fileNames) ? item.fileNames : [item.fileName]
  return [...new Set(source.map(value => String(value || '').trim()).filter(Boolean))].length
}

function specialSummary(item) {
  const assignments = item.assignments || []
  const screenCount = assignments.length
  const fileCount = assignments.reduce((sum, assignment) => sum + assignmentFileCount(assignment), 0)
  return `${screenCount} 个屏幕配置 / ${fileCount} 个文件`
}

function openQaEditor(item = null) {
  qaDialog.form = item
    ? {
        id: item.id,
        question: item.question || '',
        answer: item.answer || '',
        keywordsText: (item.keywords || []).join('\n'),
        enabled: item.enabled !== false,
        createdAt: item.createdAt || '',
      }
    : { id: '', question: '', answer: '', keywordsText: '', enabled: true, createdAt: '' }
  qaDialog.visible = true
}

function isWebCandidateExpired(item) {
  if (!item?.expiresAt) return false
  const expiresAt = Date.parse(item.expiresAt)
  return Number.isFinite(expiresAt) && expiresAt <= Date.now()
}

function formatCandidateDate(value) {
  if (!value) return '长期'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString('zh-CN', { hour12: false })
}

function webCandidateStatusLabel(item) {
  if (item?.status === 'PROMOTED') return '已提升'
  if (item?.status === 'REJECTED') return '已拒绝'
  if (isWebCandidateExpired(item)) return '已过期'
  return item?.eligibleForPromotion ? '可提升' : '待审核'
}

function webCandidateStatusClass(item) {
  if (item?.status === 'PROMOTED') return 'promoted'
  if (item?.status === 'REJECTED') return 'rejected'
  if (isWebCandidateExpired(item)) return 'expired'
  return item?.eligibleForPromotion ? 'eligible' : 'pending'
}

function webCandidateTypeLabel(item) {
  return {
    ENTERPRISE_KB: '企业内部事实',
    PUBLIC_STABLE: '公共稳定事实',
    PUBLIC_TIME_BOUND: '有年份的公共事件',
    PUBLIC_VOLATILE: '实时易变信息',
  }[item?.candidateType] || '公共稳定事实'
}

function webCandidateSourceLabel(source) {
  if (source?.manuallyVerified) return '已由管理员针对本候选核验'
  if (source?.canonicalOfficial || source?.sourceType === 'official') return '官方来源'
  if (source?.sourceType === 'reputable' || Number(source?.authorityScore || 0) >= 0.8) return '系统识别为可靠来源'
  return '普通网站：已有来源，但尚未达到可靠等级'
}

function webCandidateUnmetReasons(item) {
  if (!item) return ['候选不存在']
  const reasons = [...(Array.isArray(item.unmetReasons) ? item.unmetReasons : [])]
  if (item.status !== 'CANDIDATE') reasons.push(webCandidateStatusLabel(item))
  if (isWebCandidateExpired(item)) reasons.push('候选已经过期')
  return [...new Set(reasons)]
}

function canPromoteWebCandidate(item) {
  return Boolean(
    item
    && item.status === 'CANDIDATE'
    && item.eligibleForPromotion
    && !isWebCandidateExpired(item)
  )
}

function openWebCandidateReview(item) {
  webCandidateReview.item = item
  webCandidateReview.question = item?.suggestedQuestion || item?.question || ''
  webCandidateReview.answer = item?.answer || ''
  webCandidateReview.saving = false
  webCandidateReview.visible = true
}

async function toggleWebCandidateSourceVerification(item, source) {
  const actionId = `${item.id}:${source.url}`
  webCandidateActionId.value = actionId
  try {
    const data = await fetchJson('/api/web-qa-candidates/verify-source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: item.id,
        url: source.url,
        verified: !source.manuallyVerified,
      }),
    })
    const updated = data.item
    const index = webQaCandidates.value.findIndex(candidate => candidate.id === updated.id)
    if (index >= 0) webQaCandidates.value[index] = updated
    webCandidateReview.item = updated
    ElMessage.success(source.manuallyVerified ? '已取消人工核验' : '该来源已针对当前候选标记为可靠')
  } catch (error) {
    ElMessage.error(`来源核验更新失败：${error.message || error}`)
  } finally {
    webCandidateActionId.value = ''
  }
}

async function promoteWebCandidate() {
  const item = webCandidateReview.item
  if (!canPromoteWebCandidate(item)) {
    ElMessage.warning(`暂不可提升：${webCandidateUnmetReasons(item).join('；')}`)
    return
  }
  const question = webCandidateReview.question.trim()
  const answer = webCandidateReview.answer.trim()
  if (!question || !answer) {
    ElMessage.warning('正式问题和答案不能为空')
    return
  }
  await ElMessageBox.confirm('确认将核对后的问题和答案写入正式 QA？', '提升联网候选', { type: 'warning' })
  webCandidateReview.saving = true
  webCandidateActionId.value = item.id
  try {
    await fetchJson('/api/web-qa-candidates/promote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: item.id, question, answer }),
    })
    webCandidateReview.visible = false
    await Promise.all([refreshWebQaCandidates(), refreshQa(), refreshKnowledgeStatus()])
    qaKeyword.value = question
    qaPage.value = 1
    knowledgeMode.value = 'qa'
    ElMessage.success('候选已提升到正式 QA')
  } catch (error) {
    ElMessage.error(`提升失败：${error.message || error}`)
  } finally {
    webCandidateReview.saving = false
    webCandidateActionId.value = ''
  }
}

async function rejectWebCandidate(item) {
  if (!item || item.status !== 'CANDIDATE') return
  await ElMessageBox.confirm(`确定拒绝候选“${item.question}”？`, '拒绝联网候选', { type: 'warning' })
  webCandidateActionId.value = item.id
  webCandidateReview.saving = true
  try {
    await fetchJson('/api/web-qa-candidates/reject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: item.id }),
    })
    if (webCandidateReview.item?.id === item.id) webCandidateReview.visible = false
    await refreshWebQaCandidates()
    ElMessage.success('候选已拒绝')
  } catch (error) {
    ElMessage.error(`拒绝失败：${error.message || error}`)
  } finally {
    webCandidateReview.saving = false
    webCandidateActionId.value = ''
  }
}

async function saveQa() {
  if (!qaDialog.form.question.trim() || !qaDialog.form.answer.trim()) {
    ElMessage.warning('问题和答案不能为空')
    return
  }
  qaDialog.saving = true
  try {
    await fetchJson('/api/qa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: qaDialog.form.id || undefined,
        question: qaDialog.form.question,
        answer: qaDialog.form.answer,
        keywords: qaDialog.form.keywordsText,
        enabled: qaDialog.form.enabled,
        createdAt: qaDialog.form.createdAt || undefined,
      }),
    })
    qaDialog.visible = false
    await refreshQa()
    ElMessage.success('QA问答已保存')
  } finally {
    qaDialog.saving = false
  }
}

async function toggleQa(item, enabled) {
  await fetchJson('/api/qa', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...item, enabled }),
  })
  await refreshQa()
}

function toggleQaSelection(id, checked) {
  if (!id) return
  const next = new Set(selectedQaIds.value)
  if (checked) next.add(id)
  else next.delete(id)
  selectedQaIds.value = [...next]
}

function toggleCurrentQaPageSelection(checked) {
  const next = new Set(selectedQaIds.value)
  for (const item of pagedQaItems.value) {
    if (!item.id) continue
    if (checked) next.add(item.id)
    else next.delete(item.id)
  }
  selectedQaIds.value = [...next]
}

async function batchUpdateQa(enabled) {
  if (!selectedQaIds.value.length) return
  qaBatching.value = true
  try {
    await fetchJson('/api/qa/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: enabled ? 'enable' : 'disable', ids: selectedQaIds.value }),
    })
    selectedQaIds.value = []
    await refreshQa()
    ElMessage.success(enabled ? '已批量启用' : '已批量停用')
  } finally {
    qaBatching.value = false
  }
}

async function batchRemoveQa() {
  if (!selectedQaIds.value.length) return
  await ElMessageBox.confirm(`确定删除选中的 ${selectedQaIds.value.length} 条 QA？`, '批量删除QA问答', { type: 'warning' })
  qaBatching.value = true
  try {
    await fetchJson('/api/qa/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete', ids: selectedQaIds.value }),
    })
    selectedQaIds.value = []
    await refreshQa()
    ElMessage.success('已批量删除')
  } finally {
    qaBatching.value = false
  }
}

async function removeQa(item) {
  await ElMessageBox.confirm(`确定删除“${item.question}”？`, '删除QA问答', { type: 'warning' })
  await fetchJson(`/api/qa?id=${encodeURIComponent(item.id)}`, { method: 'DELETE' })
  await refreshQa()
  ElMessage.success('QA问答已删除')
}

function openFallbackEditor(item = null) {
  fallbackDialog.form = item
    ? {
        id: item.id,
        name: item.name || '',
        category: item.category || '通用',
        answer: item.answer || '',
        keywordsText: (item.keywords || []).join('\n'),
        excludeKeywordsText: (item.excludeKeywords || []).join('\n'),
        matchMode: item.matchMode === 'all' ? 'all' : 'any',
        minKeywordHits: Number(item.minKeywordHits) || 1,
        threshold: Number(item.threshold) || 60,
        priority: Number(item.priority) || 0,
        scopeType: item.scopeType || 'global',
        scopeValue: item.scopeValue || '',
        actionType: item.actionType || 'answer',
        enabled: item.enabled !== false,
        createdAt: item.createdAt || '',
      }
    : {
        id: '', name: '', category: '通用', answer: '', keywordsText: '', excludeKeywordsText: '',
        matchMode: 'any', minKeywordHits: 1, threshold: 60, priority: 100,
        scopeType: 'global', scopeValue: '', actionType: 'answer', enabled: true, createdAt: '',
      }
  fallbackDialog.visible = true
}

async function saveFallback() {
  const form = fallbackDialog.form
  if (!form.name.trim() || !form.answer.trim() || !form.keywordsText.trim()) {
    ElMessage.warning('规则名称、触发词和兜底话术不能为空')
    return
  }
  if (form.scopeType !== 'global' && !form.scopeValue.trim()) {
    ElMessage.warning('非全局规则需要填写范围值')
    return
  }
  fallbackDialog.saving = true
  try {
    await fetchJson('/api/fallback-rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: form.id || undefined,
        name: form.name,
        category: form.category,
        answer: form.answer,
        keywords: form.keywordsText,
        excludeKeywords: form.excludeKeywordsText,
        matchMode: form.matchMode,
        minKeywordHits: form.minKeywordHits,
        threshold: form.threshold,
        priority: form.priority,
        scopeType: form.scopeType,
        scopeValue: form.scopeType === 'global' ? '' : form.scopeValue,
        actionType: form.actionType,
        enabled: form.enabled,
        createdAt: form.createdAt || undefined,
      }),
    })
    fallbackDialog.visible = false
    await refreshFallbackRules()
    ElMessage.success('兜底规则已保存')
  } finally {
    fallbackDialog.saving = false
  }
}

async function toggleFallback(item, enabled) {
  await fetchJson('/api/fallback-rules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...item, enabled }),
  })
  await refreshFallbackRules()
}

async function removeFallback(item) {
  await ElMessageBox.confirm(`确定删除兜底规则“${item.name}”？`, '删除兜底规则', { type: 'warning' })
  await fetchJson(`/api/fallback-rules?id=${encodeURIComponent(item.id)}`, { method: 'DELETE' })
  await refreshFallbackRules()
  ElMessage.success('兜底规则已删除')
}

async function testFallbackRule() {
  if (!fallbackTestText.value.trim()) {
    ElMessage.warning('请先输入模拟问题')
    return
  }
  fallbackTesting.value = true
  try {
    fallbackTestResult.value = await fetchJson('/api/fallback-rules/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: fallbackTestText.value, target: fallbackTestTarget.value }),
    })
  } finally {
    fallbackTesting.value = false
  }
}

function fallbackScopeLabel(item) {
  const names = { global: '全局', ppt: 'PPT', special: '专场', screen: '屏幕' }
  const label = names[item.scopeType] || '全局'
  return item.scopeType === 'global' ? label : `${label}：${item.scopeValue || '-'}`
}

function fallbackActionLabel(actionType) {
  return ({ answer: '直接回答', handoff: '转专家', redirect: '拉回展项' })[actionType] || '直接回答'
}

function openWebSearchEditor(item = null) {
  webSearchDialog.form = item
    ? {
        id: item.id,
        name: item.name || '',
        category: item.category || '实时',
        keywordsText: (item.keywords || []).join('\n'),
        matchPatternsText: (item.matchPatterns || []).join('\n'),
        excludeKeywordsText: (item.excludeKeywords || []).join('\n'),
        queryTemplate: item.queryTemplate || '{question}',
        matchMode: item.matchMode === 'all' ? 'all' : 'any',
        minKeywordHits: Number(item.minKeywordHits) || 1,
        threshold: Number(item.threshold) || 60,
        priority: Number(item.priority) || 0,
        routeMode: item.routeMode === 'MUST_WEB' ? 'MUST_WEB' : 'PREFER_WEB',
        recencyDays: Number(item.recencyDays) || 0,
        maxResults: Number(item.maxResults) || 5,
        answerPrefix: item.answerPrefix || '根据最新公开信息',
        enabled: item.enabled !== false,
        createdAt: item.createdAt || '',
      }
    : {
        id: '', name: '', category: '实时', keywordsText: '', matchPatternsText: '', excludeKeywordsText: '',
        queryTemplate: '{question}', matchMode: 'any', minKeywordHits: 1, threshold: 60,
        priority: 100, routeMode: 'PREFER_WEB', recencyDays: 30, maxResults: 5, answerPrefix: '根据最新公开信息',
        enabled: true, createdAt: '',
      }
  webSearchDialog.visible = true
}

async function saveWebSearchRule() {
  const form = webSearchDialog.form
  if (!form.name.trim() || (!form.keywordsText.trim() && !form.matchPatternsText.trim())) {
    ElMessage.warning('规则名称不能为空，触发词和问题形态至少填写一项')
    return
  }
  webSearchDialog.saving = true
  try {
    await fetchJson('/api/web-search/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: form.id || undefined,
        name: form.name,
        category: form.category,
        keywords: form.keywordsText,
        matchPatterns: form.matchPatternsText,
        excludeKeywords: form.excludeKeywordsText,
        queryTemplate: form.queryTemplate,
        matchMode: form.matchMode,
        minKeywordHits: form.minKeywordHits,
        threshold: form.threshold,
        priority: form.priority,
        routeMode: form.routeMode,
        recencyDays: form.recencyDays,
        maxResults: form.maxResults,
        answerPrefix: form.answerPrefix,
        enabled: form.enabled,
        createdAt: form.createdAt || undefined,
      }),
    })
    webSearchDialog.visible = false
    await refreshWebSearchRules()
    ElMessage.success('联网搜索规则已保存')
  } finally {
    webSearchDialog.saving = false
  }
}

async function toggleWebSearchRule(item, enabled) {
  await fetchJson('/api/web-search/rules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...item, enabled }),
  })
  await refreshWebSearchRules()
}

async function removeWebSearchRule(item) {
  await ElMessageBox.confirm(`确定删除联网搜索规则“${item.name}”？`, '删除联网搜索规则', { type: 'warning' })
  await fetchJson(`/api/web-search/rules?id=${encodeURIComponent(item.id)}`, { method: 'DELETE' })
  await refreshWebSearchRules()
  ElMessage.success('联网搜索规则已删除')
}

async function testWebSearchRule() {
  if (!webSearchTestText.value.trim()) {
    ElMessage.warning('请先输入模拟问题')
    return
  }
  webSearchTesting.value = true
  try {
    webSearchTestResult.value = await fetchJson('/api/web-search/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: webSearchTestText.value }),
    })
    if (webSearchTestResult.value?.ok && webSearchTestResult.value?.matched && !webSearchTestResult.value?.result) {
      ElMessage.warning('规则已命中，但搜索服务未返回结果')
    }
  } catch (err) {
    ElMessage.error(`联网搜索测试失败：${err.message || err}`)
  } finally {
    webSearchTesting.value = false
  }
}

async function saveTtsVoice() {
  ttsSaving.value = true
  try {
    const data = await fetchJson('/api/tts-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: ttsState.provider, voice: ttsState.voice, avatarEnabled: ttsState.avatarEnabled }),
    })
    if (!data.ok) throw new Error(data.error || '保存失败')
    markTtsSaved(data)
    ttsState.provider = data.provider || ttsState.provider
    ttsState.providerName = data.providerName || ttsState.providerName
    ttsState.providers = data.providers || ttsState.providers
    ttsState.voice = data.voice || ttsState.voice
    ttsState.voices = data.voices || ttsState.voices
    ttsState.allVoices = data.allVoices || ttsState.allVoices
    ttsState.avatarEnabled = data.avatarEnabled === true
    ElMessage.success('TTS设置已保存，下一次屏幕播报会使用新配置')
  } catch (error) {
    ElMessage.error(error.message || '保存失败')
  } finally {
    ttsSaving.value = false
  }
}

async function saveAvatarTtsSwitch(value) {
  avatarTtsSaving.value = true
  try {
    const data = await fetchJson('/api/tts-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: ttsState.provider, voice: ttsState.voice, avatarEnabled: value === true }),
    })
    ttsState.avatarEnabled = data.avatarEnabled === true
    ElMessage.success(ttsState.avatarEnabled ? '数字人播报已开启，普通 TTS 已停用' : '数字人播报已关闭，恢复讯飞普通 TTS')
  } catch (error) {
    ttsState.avatarEnabled = !value
    ElMessage.error(`保存播报开关失败：${error.message || error}`)
  } finally {
    avatarTtsSaving.value = false
  }
}

async function refreshAvatarTarget(silent = true) {
  try {
    const data = await fetchJson('/api/avatar-target')
    Object.assign(avatarTarget, data)
  } catch (error) {
    if (!silent) ElMessage.error(error.message || '数字人屏幕状态获取失败')
  }
}

async function selectAvatarTarget(target) {
  avatarTargetBusy.value = true
  try {
    const data = await fetchJson('/api/avatar-target', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target }),
    })
    Object.assign(avatarTarget, data)
    ElMessage.success(`数字人已切换到${data.label}`)
  } catch (error) {
    await refreshAvatarTarget(true)
    ElMessage.error(error.message || '数字人屏幕切换失败')
  } finally {
    avatarTargetBusy.value = false
  }
}

async function testTtsVoice() {
  stopTtsPreview()
  const request = ttsPreviewAbort = new AbortController()
  ttsTesting.value = true
  try {
    const data = await fetchJson('/api/tts', {
      method: 'POST', signal: request.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: ttsPreviewText.value, provider: ttsState.provider, voice: ttsState.voice, preview: true }),
    })
    if (request.signal.aborted) return
    if (!data.ok) throw new Error(data.error || '试听失败')
    if (data.audio?.url && ttsAudio.value) {
      ttsAudio.value.src = new URL(data.audio.url, config.fileServerUrl).href
      await ttsAudio.value.play()
    }
  } catch (error) {
    if (!request.signal.aborted) ElMessage.error(error.message || '试听失败')
  } finally {
    if (ttsPreviewAbort === request) { ttsTesting.value = false; ttsPreviewAbort = null }
  }
}

function openCommentaryEditor(file) {
  commentaryDialog.file = file
  commentaryDialog.text = file.commentary || ''
  commentaryDialog.level = 'brief'
  const pageCount = resolveCommentaryPageCount(file)
  commentaryDialog.pageCount = pageCount
  commentaryDialog.pages = buildCommentaryPages(file, pageCount)
  commentaryDialog.visible = true
}

function resolveCommentaryPageCount(file) {
  const pageKeys = Object.keys(file?.commentaryPages || {})
    .map(key => Number(String(key).replace(/^p/i, '')))
    .filter(Number.isFinite)
  const legacyPages = String(file?.commentary || '').split(/\s*&&\s*/).filter(Boolean).length
  return Math.max(1, Number(file?.slideCount) || 0, ...pageKeys, legacyPages)
}

function buildCommentaryPages(file, count) {
  const savedPages = file?.commentaryPages || {}
  const legacyPages = String(file?.commentary || '').split(/\s*&&\s*/)
  return Array.from({ length: count }, (_, index) => {
    const page = index + 1
    const text = savedPages[String(page)] || savedPages[`p${page}`] || legacyPages[index] || ''
    return {
      page,
      levels: parseCommentaryLevels(text),
    }
  })
}

function emptyCommentaryLevels() {
  return { brief: '', standard: '', detail: '' }
}

function parseCommentaryLevels(value = '') {
  const source = String(value || '').trim()
  const levels = emptyCommentaryLevels()
  if (!source) return levels

  const markerPattern = /【(精炼版|标准版|详细版)】/g
  const matches = [...source.matchAll(markerPattern)]
  if (!matches.length) {
    levels.brief = source
    return levels
  }

  const keyMap = { 精炼版: 'brief', 标准版: 'standard', 详细版: 'detail' }
  matches.forEach((match, index) => {
    const start = match.index + match[0].length
    const end = index + 1 < matches.length ? matches[index + 1].index : source.length
    const key = keyMap[match[1]]
    if (key) levels[key] = source.slice(start, end).trim()
  })

  return levels
}

function formatCommentaryLevels(levels = emptyCommentaryLevels()) {
  return commentaryLevels
    .map(level => {
      const text = String(levels[level.key] || '').trim()
      return text ? `【${level.label}版】\n${text}` : ''
    })
    .filter(Boolean)
    .join('\n\n')
}

function resizeCommentaryPages() {
  const count = Math.max(1, Math.floor(Number(commentaryDialog.pageCount) || 1))
  commentaryDialog.pageCount = count
  const oldPages = new Map(commentaryDialog.pages.map(page => [page.page, page.levels]))
  commentaryDialog.pages = Array.from({ length: count }, (_, index) => {
    const page = index + 1
    return { page, levels: oldPages.get(page) || emptyCommentaryLevels() }
  })
}

function commentaryPagesPayload() {
  return commentaryDialog.pages.reduce((acc, page) => {
    const text = formatCommentaryLevels(page.levels)
    if (text) acc[String(page.page)] = text
    return acc
  }, {})
}

function hasAnyCommentary(file) {
  if (String(file?.commentary || '').trim()) return true
  return Object.values(file?.commentaryPages || {}).some(text => String(text || '').trim())
}

async function saveCommentary() {
  if (!commentaryDialog.file) return
  commentaryDialog.saving = true
  try {
    const isPpt = commentaryDialog.file.type === 'ppt'
    const pages = isPpt ? commentaryPagesPayload() : {}
    const commentary = isPpt
      ? Object.keys(pages).map(Number).sort((a, b) => a - b).map(page => pages[String(page)]).join('&&')
      : commentaryDialog.text
    const data = await fetchJson('/api/files/commentary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: commentaryDialog.file.name,
        commentary,
        commentaryPages: isPpt ? pages : undefined,
        slideCount: isPpt ? commentaryDialog.pageCount : undefined,
      }),
    })
    const updated = data.file
    mediaFiles.value = mediaFiles.value.map(item =>
      item.name === commentaryDialog.file.name
        ? {
            ...item,
            commentary: updated?.commentary ?? commentary,
            commentaryPages: updated?.commentaryPages ?? pages,
            slideCount: updated?.slideCount ?? (isPpt ? commentaryDialog.pageCount : item.slideCount),
            hasCommentary: hasAnyCommentary(updated || { commentary, commentaryPages: pages }),
          }
        : item
    )
    commentaryDialog.visible = false
    ElMessage.success('讲解词已保存')
  } finally {
    commentaryDialog.saving = false
  }
}

function sendHome(device) {
  sendCommand('home', device.screenNumber, {})
}

function sendTestCommand(device) {
  const screenNumber = device.screenNumber || ''
  sendCommand('speak', device.screenNumber, {
    text: `${screenNumber}号屏测试播报，当前语音服务连接正常。`,
    preserveView: true,
    testSpeak: true
  })
  ElMessage.success(`${screenNumber || '该'}号屏测试播报已发送`)
}

function openScreenSettings(screen) {
  const settings = screen.settings || {}
  screenSettingsDialog.deviceId = screen.deviceId || screen.id || ''
  screenSettingsDialog.form.name = settings.name || screen.name || ''
  screenSettingsDialog.form.snapshotIntervalSeconds = Number(settings.snapshotIntervalSeconds) || 5
  screenSettingsDialog.form.statusIntervalSeconds = Number(settings.statusIntervalSeconds) || 1
  screenSettingsDialog.visible = true
}

async function saveScreenSettings() {
  if (!screenSettingsDialog.deviceId) return
  screenSettingsDialog.saving = true
  try {
    const data = await fetchJson('/api/screens/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        deviceId: screenSettingsDialog.deviceId,
        name: screenSettingsDialog.form.name,
        snapshotIntervalSeconds: screenSettingsDialog.form.snapshotIntervalSeconds,
        statusIntervalSeconds: screenSettingsDialog.form.statusIntervalSeconds,
      }),
    })
    if (data.screen) upsertDevice({ ...data.screen, id: data.screen.deviceId, type: 'client' })
    screenSettingsDialog.visible = false
    ElMessage.success(data.synced ? '设置已保存并同步到屏幕' : '设置已保存，屏幕上线后自动同步')
    refreshDevices()
  } catch (err) {
    ElMessage.error(`保存失败：${err.message || err}`)
  } finally {
    screenSettingsDialog.saving = false
  }
}

function sendCommand(action, target, params) {
  if (ws?.readyState !== WebSocket.OPEN) {
    ElMessage.warning('中控未连接')
    return
  }
  ws.send(JSON.stringify({ type: 'command', action, target, params }))
}

function sendSimulatorQuestion() {
  const text = simulatorText.value.trim()
  if (!text) {
    ElMessage.warning('请输入要模拟的问题')
    return
  }
  if (ws?.readyState !== WebSocket.OPEN) {
    ElMessage.warning('中控未连接')
    return
  }

  const requestId = globalThis.crypto?.randomUUID?.()
    || `admin-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  simulatorRequestId.value = requestId
  if (!simulatorConversationId.value) {
    simulatorConversationId.value = globalThis.crypto?.randomUUID?.() || `conversation-${Date.now()}`
    selectedConversationId.value = simulatorConversationId.value
  }
  archivedConversationIds.value = new Set(
    [...archivedConversationIds.value].filter(id => id !== simulatorConversationId.value),
  )
  const conversation = upsertConversation({
    id: `request-${requestId}`,
    requestId,
    conversationId: simulatorConversationId.value,
    deviceId: 'admin-simulator',
    deviceType: 'admin',
    source: 'admin-simulator',
    input: text,
    visibleInput: '',
    reply: '',
    progressStage: 'RECEIVED',
    progressMessage: '已收到问题',
    pending: true,
    time: Date.now(),
    questionTime: Date.now(),
  })
  animateConversationField(conversation.id, 'visibleInput', text, 18)

  simulatorSending.value = true
  ws.send(JSON.stringify({
    type: 'chat',
    text,
    target: simulatorTarget.value || 'all',
    source: 'admin-simulator',
    requestId,
    conversationId: simulatorConversationId.value,
  }))
  simulatorText.value = ''
  setTimeout(() => {
    simulatorSending.value = false
  }, 800)
}

function startNewSimulatorConversation() {
  simulatorConversationId.value = globalThis.crypto?.randomUUID?.() || `conversation-${Date.now()}`
  selectedConversationId.value = simulatorConversationId.value
  simulatorRequestId.value = ''
  simulatorText.value = ''
}

function selectConversationSession(session) {
  if (!session?.id) return
  selectedConversationId.value = session.id
  simulatorConversationId.value = session.id.startsWith('legacy-') ? '' : session.id
  simulatorRequestId.value = ''
}

async function renameConversationSession(session) {
  if (!session?.id) return
  try {
    const { value } = await ElMessageBox.prompt('请输入新的会话名称', '重命名会话', {
      inputValue: session.title || '',
      inputPlaceholder: '最多60个字符',
      inputValidator: input => {
        const title = String(input || '').trim()
        if (!title) return '会话名称不能为空'
        if (title.length > 60) return '会话名称不能超过60个字符'
        return true
      },
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    const title = String(value || '').trim()
    const result = await fetchJson('/api/conversation-sessions', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: session.id, title }),
    })
    conversations.value = conversations.value.map(item => {
      const itemSessionId = item.conversationId || `legacy-${item.id}`
      return itemSessionId === session.id
        ? { ...item, conversationTitle: result.title || title }
        : item
    })
    ElMessage.success('会话名称已更新')
  } catch (err) {
    if (err === 'cancel' || err === 'close' || err?.message === 'cancel') return
    ElMessage.error(`重命名失败：${err?.message || err}`)
  }
}

async function deleteConversationSession(session) {
  if (!session?.id) return
  await ElMessageBox.confirm(`确定删除会话“${session.title || '未命名会话'}”及其问答记录？`, '删除会话', {
    type: 'warning',
  })
  await fetchJson(`/api/conversation-sessions?id=${encodeURIComponent(session.id)}`, {
    method: 'DELETE',
  })
  conversations.value = conversations.value.filter(item =>
    session.id.startsWith('legacy-')
      ? `legacy-${item.id}` !== session.id
      : item.conversationId !== session.id)
  archivedConversationIds.value = new Set(
    [...archivedConversationIds.value].filter(id => id !== session.id),
  )
  if (simulatorConversationId.value === session.id) {
    startNewSimulatorConversation()
  } else {
    selectedConversationId.value = conversationSessions.value[0]?.id || ''
  }
  ElMessage.success('会话已删除')
}

function typeLabel(type) {
  return { ppt: 'PPT', video: '视频', image: '图片' }[type] || type || '文件'
}

function pageCountLabel(file) {
  if (file.type !== 'ppt') return '-'
  return file.slideCount ? `${file.slideCount} 页` : '未知'
}

function assignmentFileLabel(fileName) {
  const file = mediaFiles.value.find(item => item.name === fileName)
  if (!file) return fileName
  return `${typeLabel(file.type)} | ${file.title || file.name}`
}

function snapshotUrl(row) {
  const url = row.snapshot?.url || ''
  if (!url) return ''
  if (/^https?:\/\//i.test(url)) return url
  return `${config.fileServerUrl.replace(/\/+$/, '')}${url}`
}

function voiceFullName(key) {
  return ttsState.voices[key] || key
}

function formatTime(ts) {
  if (!ts) return '-'
  return new Date(ts).toLocaleTimeString('zh-CN')
}

function formatConversationTime(ts) {
  if (!ts) return '-'
  const date = new Date(ts)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', { hour12: false })
}

function formatSnapshotTime(ts) {
  if (!ts) return ''
  return `更新于 ${new Date(ts).toLocaleTimeString('zh-CN')}`
}

function formatPercent(value) {
  const num = Number(value)
  return Number.isFinite(num) ? `${num.toFixed(1)}%` : '--'
}

function percentValue(value) {
  const percent = Number(value)
  return Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : null
}

function isCpuUnhealthy(status = {}) {
  const safeStatus = status || {}
  const percent = percentValue(safeStatus.cpuPercent)
  return percent !== null && percent >= 85
}

function cpuBarWidth(status = {}) {
  const safeStatus = status || {}
  const percent = percentValue(safeStatus.cpuPercent)
  return percent === null ? '0%' : `${percent}%`
}

function memoryPercentValue(status = {}) {
  const safeStatus = status || {}
  const percent = percentValue(safeStatus.memoryPercent)
  if (percent !== null) return percent
  const used = Number(safeStatus.memoryUsedMb)
  const total = Number(safeStatus.memoryTotalMb)
  if (!Number.isFinite(used) || !Number.isFinite(total) || total <= 0) return null
  return Math.max(0, Math.min(100, (used / total) * 100))
}

function isMemoryUnhealthy(status = {}) {
  const percent = memoryPercentValue(status)
  return percent !== null && percent >= 85
}

function memoryBarWidth(status = {}) {
  const percent = memoryPercentValue(status)
  return percent === null ? '0%' : `${percent}%`
}

watch(onlineScreens, () => {
  ensureSelectedSpecialAssignments()
})

watch(knowledgeMode, mode => {
  if (mode === 'webCandidates') refreshWebQaCandidates()
})

onMounted(() => {
  connectWS()
  refreshAll()
  refreshTimer = setInterval(() => {
    refreshDevices()
    if (activeTab.value === 'avatar') refreshAvatarStatus(true)
  }, 1000)
})

onUnmounted(() => {
  ws?.close()
  clearInterval(heartbeatTimer)
  clearInterval(refreshTimer)
  for (const timer of conversationTypingTimers.values()) clearInterval(timer)
  conversationTypingTimers.clear()
})
</script>

<style scoped>
* { box-sizing: border-box; }
:global(body) { margin: 0; }
.admin-app {
  min-height: 100vh;
  display: flex;
  color: #d9fbff;
  background:
    linear-gradient(135deg, rgba(11, 38, 59, .94), rgba(3, 16, 28, .98)),
    repeating-linear-gradient(0deg, transparent 0 63px, rgba(0, 214, 255, .08) 64px),
    repeating-linear-gradient(90deg, transparent 0 63px, rgba(0, 214, 255, .08) 64px);
  font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif;
}
.sidebar {
  width: 260px;
  padding: 24px 18px;
  border-right: 1px solid rgba(0, 214, 255, .22);
  background: rgba(4, 20, 33, .78);
}
.brand { display: flex; gap: 12px; align-items: center; margin-bottom: 30px; }
.brand-mark {
  width: 42px; height: 42px; display: grid; place-items: center;
  border: 1px solid #20d6ff; color: #25f5d0; font-weight: 800;
}
.brand-title { font-size: 18px; font-weight: 700; }
.brand-subtitle { font-size: 11px; color: #78aab6; text-transform: uppercase; }
.nav-item {
  width: 100%; display: flex; justify-content: space-between; align-items: center;
  min-height: 48px; margin-bottom: 8px; padding: 0 14px;
  color: #bdeef5; background: transparent; border: 1px solid transparent;
  cursor: pointer; text-align: left;
}
.nav-item small { color: #5c8793; }
.nav-count {
  display: inline-grid;
  min-width: 20px;
  height: 20px;
  margin-left: 7px;
  padding: 0 6px;
  place-items: center;
  border-radius: 10px;
  color: #05202a;
  background: #25f5d0;
  font-size: 11px;
  line-height: 20px;
}
.nav-item.active, .nav-item:hover {
  border-color: rgba(35, 226, 255, .6);
  background: rgba(0, 214, 255, .1);
}
.connection {
  margin-top: 30px; padding: 12px 14px; border: 1px solid rgba(255,255,255,.12);
  color: #ffb4b4;
}
.connection.online { color: #6fffd3; }
.status-dot {
  display: inline-block; width: 8px; height: 8px; margin-right: 8px; border-radius: 50%;
  background: currentColor; box-shadow: 0 0 12px currentColor;
}
.main { flex: 1; padding: 26px; min-width: 0; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
h1, h2, p { margin: 0; }
h1 { font-size: 28px; letter-spacing: 0; }
h2 { font-size: 20px; }
.topbar p, .panel-header p, .knowledge-head p { margin-top: 6px; color: #7faeba; }
.top-actions, .filter-row, .voice-test, .panel-actions { display: flex; gap: 10px; align-items: center; }
.panel-actions :deep(.el-button.is-text) {
  height: 32px;
  padding: 0 12px;
  color: #d9fbff;
  background: rgba(0, 214, 255, .12);
  border: 1px solid rgba(37, 240, 207, .34);
  border-radius: 2px;
}
.panel-actions :deep(.el-button.is-text:hover),
.panel-actions :deep(.el-button.is-text:focus) {
  color: #04151e;
  background: #25f0cf;
  border-color: #25f0cf;
}
.form-unit { margin-left: 8px; color: #80aeba; }
.page { width: 100%; }
.smart-exhibition-page {
  height: calc(100vh - 116px);
  min-height: 680px;
}
.smart-exhibition-frame {
  width: 100%;
  height: 100%;
  display: block;
  border: 1px solid rgba(0, 213, 255, .22);
  background: #06131b;
}
.avatar-stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(190px, 1fr));
  gap: 16px;
  margin-bottom: 18px;
}
.avatar-state-card strong {
  font-size: 30px;
  line-height: 1.2;
}
.avatar-state-card strong.offline { color: #ff9b9b; }
.avatar-state-card .command-name { color: #5cddff; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.avatar-panel { margin-bottom: 18px; }
.avatar-actions { display: flex; flex-wrap: wrap; gap: 12px; }
.avatar-target-selector {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-bottom: 18px;
  padding: 14px 18px;
  border: 1px solid rgba(0, 214, 255, .24);
  background: rgba(0, 0, 0, .18);
}
.avatar-target-selector > span { color: #d9fbff; font-weight: 600; white-space: nowrap; }
.avatar-tts-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 18px;
  padding: 16px 18px;
  border: 1px solid rgba(0, 214, 255, .24);
  background: rgba(0, 0, 0, .18);
}
.avatar-tts-switch.enabled {
  border-color: rgba(37, 240, 207, .55);
  background: rgba(37, 240, 207, .08);
  box-shadow: 0 0 22px rgba(37, 240, 207, .08) inset;
}
.avatar-tts-switch strong { display: block; margin-bottom: 7px; color: #d9fbff; font-size: 17px; }
.avatar-tts-switch p { color: #7faeba; }
.avatar-error {
  margin-top: 14px;
  padding: 10px 12px;
  color: #ffc2c2;
  border: 1px solid rgba(255, 110, 110, .34);
  background: rgba(120, 20, 28, .22);
}
.avatar-control-grid {
  display: grid;
  grid-template-columns: minmax(420px, 1fr) minmax(420px, 1fr);
  gap: 18px;
}
.avatar-bounds-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(160px, 1fr));
  gap: 14px;
}
.avatar-bounds-grid label { display: grid; gap: 7px; color: #7faeba; font-size: 13px; }
.avatar-bounds-grid :deep(.el-input-number) { width: 100%; }
.avatar-full-button { width: 100%; margin-top: 16px; }
.avatar-speak-actions { display: flex; gap: 12px; margin-top: 14px; }
@media (max-width: 1100px) {
  .avatar-stats-grid { grid-template-columns: 1fr; }
  .avatar-control-grid { grid-template-columns: 1fr; }
}
.stats-grid {
  display: grid; grid-template-columns: repeat(3, minmax(240px, 1fr)); gap: 16px; margin-bottom: 18px;
}
.stat-card, .panel, .knowledge-shell {
  background: rgba(8, 32, 48, .82);
  border: 1px solid rgba(0, 214, 255, .26);
  box-shadow: 0 0 32px rgba(0, 214, 255, .08) inset;
}
.stat-card { padding: 18px; min-height: 120px; display: flex; flex-direction: column; gap: 8px; }
.screen-online-card { min-height: 142px; justify-content: center; }
.stat-card span, .stat-card em { color: #7faeba; font-style: normal; }
.stat-card strong { font-size: 48px; color: #28f5d0; line-height: 1.1; }
.panel, .knowledge-shell { padding: 18px; }
.panel-header, .knowledge-head, .kb-toolbar {
  display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 16px;
}
.conversation-list { display: grid; gap: 10px; }
.conversation-item { padding: 12px; background: rgba(0, 0, 0, .18); border-left: 3px solid #20d6ff; transition: border-color .2s ease, background .2s ease; }
.conversation-item.pending { border-left-color: #25f5d0; background: rgba(0, 214, 255, .07); }
.conversation-meta { color: #6c9fac; font-size: 12px; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.conversation-progress-stage {
  color: #021a21;
  background: #25f5d0;
  border: 1px solid rgba(37, 245, 208, .7);
  padding: 1px 7px;
  font-weight: 700;
}
.conversation-source { color: #001a1f; background: #25f0cf; border: 1px solid rgba(37, 240, 207, .65); padding: 1px 7px; font-weight: 700; }
.conversation-route { color: #9adbe8; border: 1px solid rgba(145, 244, 255, .28); padding: 1px 7px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.conversation-question { color: #20d6ff; margin-bottom: 4px; }
.conversation-answer { color: #ffffff; }
.chat-page { min-height: calc(100vh - 150px); }
.chat-workspace {
  display: grid;
  grid-template-columns: 240px minmax(460px, 1fr) 300px;
  min-height: calc(100vh - 170px);
  border: 1px solid rgba(0, 214, 255, .25);
  background: rgba(4, 24, 38, .84);
}
.chat-session-rail,
.chat-diagnostic-panel {
  min-width: 0;
  padding: 16px;
  background: rgba(2, 17, 29, .72);
}
.chat-session-rail { border-right: 1px solid rgba(0, 214, 255, .18); }
.chat-diagnostic-panel { border-left: 1px solid rgba(0, 214, 255, .18); overflow-y: auto; }
.new-chat-button { width: 100%; margin-bottom: 14px; }
.chat-session-list { display: grid; gap: 8px; max-height: calc(100vh - 250px); overflow-y: auto; }
.chat-session-item {
  position: relative;
  display: grid;
  gap: 5px;
  width: 100%;
  padding: 11px 104px 11px 12px;
  color: #a6d9e3;
  text-align: left;
  background: rgba(0, 214, 255, .04);
  border: 1px solid rgba(0, 214, 255, .13);
  cursor: pointer;
}
.chat-session-item.active { background: rgba(37, 245, 208, .1); border-color: rgba(37, 245, 208, .5); }
.chat-session-item strong { color: #effeff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chat-session-item span { color: #6fabb8; font-size: 12px; }
.chat-session-item em { color: #25f5d0; font-size: 11px; font-style: normal; }
.chat-session-actions {
  position: absolute;
  top: 9px;
  right: 9px;
  display: flex;
  gap: 4px;
}
.chat-session-rename,
.chat-session-delete {
  padding: 3px 7px;
  font-size: 11px;
  border-radius: 4px;
  cursor: pointer;
}
.chat-session-rename { color: #8eeaff; background: rgba(40, 200, 255, .08); border: 1px solid rgba(40, 200, 255, .28); }
.chat-session-delete { color: #ff9da7; background: rgba(255, 88, 104, .08); border: 1px solid rgba(255, 88, 104, .28); }
.chat-session-rename:hover { color: #fff; background: rgba(40, 200, 255, .24); }
.chat-session-delete:hover { color: #fff; background: rgba(255, 88, 104, .28); }
.chat-dialog-panel { display: grid; grid-template-rows: auto 1fr auto; min-width: 0; }
.chat-dialog-header { display: flex; justify-content: space-between; gap: 16px; padding: 18px 20px; border-bottom: 1px solid rgba(0, 214, 255, .16); }
.chat-dialog-header h2 { margin: 0 0 5px; color: #fff; }
.chat-dialog-header p { margin: 0; color: #73aeba; font-size: 13px; }
.chat-session-state { align-self: flex-start; padding: 3px 8px; color: #03201c; background: #25f5d0; font-size: 12px; font-weight: 700; }
.chat-session-state.archived { color: #bdd4da; background: rgba(255,255,255,.08); }
.chat-message-list { padding: 20px; overflow-y: auto; max-height: calc(100vh - 360px); }
.chat-turn { display: grid; gap: 10px; margin-bottom: 20px; }
.chat-bubble { position: relative; max-width: 86%; padding: 12px 14px; line-height: 1.7; }
.chat-bubble small { display: block; margin-bottom: 5px; font-weight: 700; opacity: .72; }
.chat-bubble p { margin: 0; white-space: pre-wrap; }
.chat-bubble::after {
  position: absolute;
  top: calc(100% + 5px);
  z-index: 6;
  width: max-content;
  max-width: 260px;
  padding: 4px 7px;
  color: #dffaff;
  font-size: 11px;
  line-height: 1.4;
  white-space: nowrap;
  pointer-events: none;
  content: attr(data-time);
  background: rgba(2, 17, 29, .96);
  border: 1px solid rgba(145, 244, 255, .25);
  border-radius: 4px;
  opacity: 0;
  transform: translateY(-2px);
  transition: opacity .15s ease, transform .15s ease;
}
.chat-bubble:hover::after { opacity: 1; transform: translateY(0); }
.user-bubble::after { right: 0; }
.assistant-bubble::after { left: 0; }
.user-bubble { justify-self: end; color: #04212a; background: linear-gradient(135deg, #25f5d0, #28c8ff); }
.assistant-bubble { justify-self: start; color: #f2feff; background: rgba(16, 55, 72, .86); border: 1px solid rgba(145, 244, 255, .16); }
.chat-composer { display: grid; grid-template-columns: 140px 1fr auto; gap: 10px; align-items: end; padding: 16px 20px; border-top: 1px solid rgba(0, 214, 255, .16); }
.chat-diagnostic-panel h3 { margin: 0 0 16px; color: #fff; }
.chat-diagnostic-panel dl { display: grid; grid-template-columns: 76px 1fr; gap: 9px; margin: 0 0 18px; font-size: 13px; }
.chat-diagnostic-panel dt { color: #6fabb8; }
.chat-diagnostic-panel dd { margin: 0; color: #e9fdff; overflow-wrap: anywhere; }
.chat-route-trace { margin-top: 14px; }
.conversation-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
  color: #a7e9f1;
}
.progress-orbit {
  display: inline-block;
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
  border: 2px solid rgba(37, 245, 208, .25);
  border-top-color: #25f5d0;
  border-radius: 50%;
  animation: progress-spin .8s linear infinite;
}
.typing-caret {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 3px;
  vertical-align: -2px;
  background: #25f5d0;
  animation: caret-blink .8s steps(1) infinite;
}
@keyframes progress-spin { to { transform: rotate(360deg); } }
@keyframes caret-blink { 0%, 48% { opacity: 1; } 49%, 100% { opacity: 0; } }
.web-candidate-stats {
  grid-template-columns: repeat(4, minmax(180px, 1fr));
}
.web-candidate-stats .stat-card strong { font-size: 38px; }
.web-candidate-page .panel { min-height: 420px; }
.knowledge-embedded-candidates { width: 100%; margin-top: 18px; }
.web-candidate-toolbar {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 150px 160px;
  gap: 12px;
  margin-bottom: 18px;
}
.web-candidate-list { display: grid; gap: 12px; }
.web-candidate-card {
  display: flex;
  justify-content: space-between;
  gap: 22px;
  padding: 16px;
  border: 1px solid rgba(0, 214, 255, .18);
  border-left: 3px solid #5c8793;
  background: rgba(0, 0, 0, .2);
}
.web-candidate-card.eligible { border-left-color: #25f5d0; }
.web-candidate-card.promoted { border-left-color: #48a9ff; }
.web-candidate-card.rejected { opacity: .7; border-left-color: #ff7d86; }
.web-candidate-main { min-width: 0; flex: 1; }
.web-candidate-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.web-candidate-title strong {
  min-width: 240px;
  flex: 1;
  color: #effdff;
  font-size: 16px;
}
.web-candidate-main > p {
  color: #aed7df;
  line-height: 1.7;
  white-space: pre-wrap;
}
.status-pill,
.candidate-type {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border: 1px solid rgba(145, 244, 255, .28);
  color: #9adbe8;
  background: rgba(145, 244, 255, .08);
  font-size: 12px;
  white-space: nowrap;
}
.status-pill.eligible,
.status-pill.promoted {
  color: #05202a;
  border-color: #25f5d0;
  background: #25f5d0;
  font-weight: 700;
}
.status-pill.rejected,
.status-pill.expired {
  color: #ffd0d3;
  border-color: rgba(255, 125, 134, .5);
  background: rgba(255, 76, 88, .12);
}
.candidate-type.volatile {
  color: #ffd48a;
  border-color: rgba(255, 190, 77, .38);
  background: rgba(255, 190, 77, .1);
}
.candidate-criteria,
.review-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.candidate-criteria span,
.review-summary > span {
  padding: 3px 8px;
  color: #84adb7;
  border: 1px solid rgba(132, 173, 183, .2);
  background: rgba(132, 173, 183, .06);
  font-size: 12px;
}
.candidate-criteria span.done {
  color: #76f5d2;
  border-color: rgba(37, 245, 208, .25);
}
.candidate-criteria span.expired,
.review-summary > span.expired {
  color: #ff9ba2;
  border-color: rgba(255, 125, 134, .35);
}
.candidate-sources {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.candidate-sources a {
  max-width: 280px;
  padding: 3px 8px;
  overflow: hidden;
  color: #91f4ff;
  border: 1px solid rgba(145, 244, 255, .2);
  background: rgba(145, 244, 255, .06);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.candidate-sources span { color: #6d98a5; font-size: 13px; }
.web-candidate-actions {
  width: 108px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  justify-content: center;
}
.web-candidate-actions :deep(.el-button) { width: 100%; margin-left: 0; }
.web-candidate-review { display: grid; gap: 18px; }
.review-summary { margin-top: 0; }
.review-sources h3 {
  margin: 0 0 10px;
  color: #d9fbff;
}
.review-sources { display: grid; gap: 8px; }
.review-source-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}
.review-sources a {
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  color: #91f4ff;
  border: 1px solid rgba(145, 244, 255, .18);
  background: rgba(2, 18, 31, .58);
  text-decoration: none;
}
.review-sources a small {
  overflow: hidden;
  color: #6d98a5;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.review-sources p { color: #6d98a5; }
@media (max-width: 760px) {
  .review-source-row { grid-template-columns: 1fr; }
  .review-source-row :deep(.el-button) { width: 100%; margin-left: 0; }
}
.web-candidate-dialog :deep(.el-dialog) {
  background: rgba(3, 22, 35, .98);
  border: 1px solid rgba(0, 214, 255, .32);
}
.web-candidate-dialog :deep(.el-dialog__header),
.web-candidate-dialog :deep(.el-dialog__body),
.web-candidate-dialog :deep(.el-dialog__footer) {
  background: transparent;
}
.web-candidate-dialog :deep(.el-dialog__title) { color: #ffffff; font-weight: 800; }
.web-candidate-dialog :deep(.el-dialog__close) { color: #8ed7e7; }
.conversation-sources { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 12px; }
.conversation-sources b { color: #7faeba; font-weight: 600; }
.conversation-sources a { color: #91f4ff; border: 1px solid rgba(145, 244, 255, .22); background: rgba(145, 244, 255, .08); padding: 2px 7px; text-decoration: none; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conversation-sources span { color: #bdeef3; border: 1px solid rgba(37, 240, 207, .24); background: rgba(37, 240, 207, .08); padding: 2px 7px; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.route-trace { margin-top: 8px; color: #719aa4; font-size: 12px; }
.route-trace summary { cursor: pointer; color: #91f4ff; }
.route-trace div { margin-top: 3px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.tag-help-dialog :deep(.el-dialog) {
  background: linear-gradient(180deg, #031018, #02090e);
  border: 1px solid rgba(37, 240, 207, .34);
  box-shadow: 0 28px 80px rgba(0, 0, 0, .62), inset 0 0 34px rgba(0, 214, 255, .06);
}
.tag-help-dialog :deep(.el-dialog__header) {
  padding: 22px 24px 12px;
  border-bottom: 1px solid rgba(0, 214, 255, .18);
}
.tag-help-dialog :deep(.el-dialog__title) { color: #ffffff; font-weight: 800; }
.tag-help-dialog :deep(.el-dialog__body) { color: #c9f7ff; background: transparent; }
.tag-help-dialog :deep(.el-dialog__close) { color: #8ed7e7; }
.commentary-dialog :deep(.el-dialog) {
  background: linear-gradient(180deg, #03131e, #020910);
  border: 1px solid rgba(37, 240, 207, .34);
  box-shadow: 0 30px 86px rgba(0, 0, 0, .66), inset 0 0 36px rgba(0, 214, 255, .06);
}
.commentary-dialog :deep(.el-dialog__header) {
  padding: 22px 24px 12px;
  border-bottom: 1px solid rgba(0, 214, 255, .18);
}
.commentary-dialog :deep(.el-dialog__title) {
  color: #ffffff;
  font-weight: 800;
}
.commentary-dialog :deep(.el-dialog__body) {
  background: transparent;
  color: #d9fbff;
}
.commentary-dialog :deep(.el-dialog__footer) {
  border-top: 1px solid rgba(0, 214, 255, .14);
}
.commentary-dialog :deep(.el-dialog__close) { color: #8ed7e7; }
.commentary-dialog :deep(.el-textarea__inner),
.commentary-dialog :deep(.el-input__wrapper) {
  color: #e7fbff;
  background: rgba(0, 19, 31, .88);
  border: 1px solid rgba(0, 214, 255, .26);
  box-shadow: none;
}
.commentary-dialog :deep(.el-textarea__inner::placeholder) { color: rgba(174, 233, 242, .48); }
.commentary-dialog :deep(.el-input-number__decrease),
.commentary-dialog :deep(.el-input-number__increase) {
  color: #9feef5;
  background: rgba(0, 214, 255, .08);
  border-color: rgba(0, 214, 255, .22);
}
:global(.commentary-dialog),
:global(.commentary-dialog.el-dialog),
:global(.commentary-dialog .el-dialog) {
  background: linear-gradient(180deg, #03131e, #020910) !important;
  border: 1px solid rgba(37, 240, 207, .34) !important;
  box-shadow: 0 30px 86px rgba(0, 0, 0, .66), inset 0 0 36px rgba(0, 214, 255, .06) !important;
}
:global(.commentary-dialog .el-dialog__header),
:global(.commentary-dialog.el-dialog .el-dialog__header) {
  background: transparent !important;
  border-bottom: 1px solid rgba(0, 214, 255, .18);
}
:global(.commentary-dialog .el-dialog__body),
:global(.commentary-dialog.el-dialog .el-dialog__body),
:global(.commentary-dialog .el-dialog__footer),
:global(.commentary-dialog.el-dialog .el-dialog__footer) {
  background: transparent !important;
}
:global(.commentary-dialog .el-dialog__title),
:global(.commentary-dialog.el-dialog .el-dialog__title) {
  color: #ffffff !important;
  font-weight: 800;
}
:global(.commentary-dialog .el-dialog__close),
:global(.commentary-dialog.el-dialog .el-dialog__close) {
  color: #8ed7e7 !important;
}
:global(.commentary-dialog .commentary-file),
:global(.commentary-dialog .commentary-page) {
  background: rgba(0, 214, 255, .055) !important;
  border-color: rgba(0, 214, 255, .2) !important;
}
:global(.commentary-dialog .segmented) {
  background: rgba(0, 16, 27, .9) !important;
  border-color: rgba(0, 214, 255, .28) !important;
}
:global(.commentary-dialog .segmented button) {
  color: #94cbd6 !important;
  background: transparent !important;
}
:global(.commentary-dialog .segmented button.active) {
  color: #04121d !important;
  background: #25f5d0 !important;
}
:global(.commentary-dialog .el-input-number__decrease),
:global(.commentary-dialog .el-input-number__increase),
:global(.commentary-dialog .el-input__wrapper),
:global(.commentary-dialog .el-textarea__inner) {
  background: rgba(0, 19, 31, .9) !important;
  border-color: rgba(0, 214, 255, .28) !important;
  box-shadow: none !important;
}
:global(.commentary-dialog .el-input__inner),
:global(.commentary-dialog .el-textarea__inner) {
  color: #e7fbff !important;
}
.commentary-editor { display: grid; gap: 16px; }
.commentary-file {
  display: grid;
  gap: 5px;
  padding: 14px 16px;
  background: rgba(0, 214, 255, .06);
  border: 1px solid rgba(0, 214, 255, .18);
}
.commentary-file strong { color: #e8feff; font-size: 18px; }
.commentary-file span { color: #83bdca; }
.commentary-page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.page-count-control {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #c6f7ff;
}
.page-count-control em {
  color: #7faeba;
  font-style: normal;
}
.commentary-level-switch button {
  min-width: 82px;
}
.commentary-pages {
  display: grid;
  gap: 14px;
  max-height: min(54vh, 560px);
  overflow: auto;
  padding-right: 6px;
}
.commentary-page {
  display: grid;
  gap: 8px;
  padding: 12px;
  background: rgba(0, 214, 255, .045);
  border: 1px solid rgba(0, 214, 255, .16);
}
.commentary-page strong {
  color: #25f5d0;
  font-size: 15px;
}
.tag-help-grid { display: grid; gap: 12px; }
.tag-help-item {
  display: grid;
  grid-template-columns: 150px 1fr;
  gap: 14px;
  align-items: start;
  padding: 12px;
  background: rgba(0, 214, 255, .07);
  border: 1px solid rgba(0, 214, 255, .22);
}
.tag-help-item .conversation-source {
  justify-self: start;
  white-space: nowrap;
}
.tag-help-item strong {
  display: block;
  color: #ffffff;
  margin-bottom: 4px;
}
.tag-help-item p {
  color: #aee9f2;
  line-height: 1.65;
}
:global(.tag-help-dialog),
:global(.tag-help-dialog.el-dialog),
:global(.tag-help-dialog .el-dialog) {
  background: linear-gradient(180deg, #031018, #02090e) !important;
  border: 1px solid rgba(37, 240, 207, .34) !important;
  box-shadow: 0 28px 80px rgba(0, 0, 0, .62), inset 0 0 34px rgba(0, 214, 255, .06) !important;
}
:global(.tag-help-dialog .el-dialog__header),
:global(.tag-help-dialog.el-dialog .el-dialog__header) {
  background: transparent !important;
  border-bottom: 1px solid rgba(0, 214, 255, .18);
}
:global(.tag-help-dialog .el-dialog__body),
:global(.tag-help-dialog.el-dialog .el-dialog__body) {
  background: transparent !important;
}
:global(.tag-help-dialog .el-dialog__title),
:global(.tag-help-dialog.el-dialog .el-dialog__title) {
  color: #ffffff !important;
  font-weight: 800;
}
:global(.tag-help-dialog .el-dialog__close),
:global(.tag-help-dialog.el-dialog .el-dialog__close) {
  color: #8ed7e7 !important;
}
.simulator-target { width: 150px; }
.floating-simulator-button {
  position: fixed;
  right: 26px;
  bottom: 26px;
  z-index: 40;
  width: 116px;
  height: 48px;
  color: #03121d;
  font-weight: 800;
  background: linear-gradient(135deg, #25f5d0, #28c8ff);
  border: 0;
  box-shadow: 0 14px 34px rgba(0, 214, 255, .25), 0 0 0 1px rgba(255,255,255,.2) inset;
  cursor: pointer;
}
.floating-simulator-button:hover {
  filter: brightness(1.06);
}
.floating-simulator-panel {
  position: fixed;
  right: 26px;
  bottom: 86px;
  z-index: 41;
  width: min(420px, calc(100vw - 52px));
  color: #d9fbff;
  background: rgba(3, 22, 35, .98);
  border: 1px solid rgba(0, 214, 255, .34);
  box-shadow: 0 24px 70px rgba(0, 0, 0, .48), 0 0 36px rgba(0, 214, 255, .1) inset;
}
.floating-simulator-panel header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px;
  border-bottom: 1px solid rgba(0, 214, 255, .18);
}
.floating-simulator-panel strong {
  display: block;
  margin-bottom: 4px;
  color: #ffffff;
  font-size: 17px;
}
.floating-simulator-panel header span {
  color: #7faeba;
  font-size: 13px;
}
.floating-simulator-panel header button {
  width: 30px;
  height: 30px;
  color: #8ddcff;
  font-size: 22px;
  line-height: 1;
  background: rgba(0, 214, 255, .08);
  border: 1px solid rgba(0, 214, 255, .2);
  cursor: pointer;
}
.floating-simulator-body {
  display: grid;
  gap: 12px;
  padding: 16px 18px 18px;
}
.floating-simulator-body .simulator-target {
  width: 100%;
}
.simulator-live-card {
  display: grid;
  gap: 9px;
  padding: 13px 14px;
  border: 1px solid rgba(37, 245, 208, .22);
  background: rgba(0, 214, 255, .06);
}
.simulator-live-head {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #25f5d0;
}
.simulator-live-head strong { margin: 0; font-size: 13px; }
.simulator-live-head small {
  min-width: 0;
  color: #7faeba;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.simulator-live-question { color: #20d6ff; font-size: 14px; }
.simulator-live-answer {
  max-height: 180px;
  overflow-y: auto;
  color: #ffffff;
  line-height: 1.65;
  white-space: pre-wrap;
}
.segmented {
  display: inline-grid;
  grid-template-columns: repeat(4, 1fr);
  padding: 4px;
  background: rgba(0, 0, 0, .2);
  border: 1px solid rgba(0, 214, 255, .24);
}
.segmented button {
  min-width: 112px;
  height: 34px;
  border: 0;
  color: #94cbd6;
  background: transparent;
  cursor: pointer;
}
.segmented button.active {
  color: #04121d;
  background: #25f5d0;
}
.kb-panel {
  border: 1px solid rgba(0, 214, 255, .18);
  background: rgba(2, 18, 30, .58);
}
.kb-toolbar {
  padding: 16px;
  margin: 0;
  border-bottom: 1px solid rgba(0, 214, 255, .18);
}
.kb-toolbar strong { display: block; font-size: 18px; margin-bottom: 5px; color: #ffffff; }
.kb-toolbar span { color: #7faeba; }
.kb-table {
  overflow-x: auto;
  overflow-y: visible;
}
.table-pagination {
  display: flex;
  justify-content: flex-end;
  padding: 14px 16px 16px;
  border-top: 1px solid rgba(0, 214, 255, .14);
}
.table-pagination.compact {
  padding: 12px 0 0;
  border-top: 0;
}
.table-pagination :deep(.el-pagination.is-background .el-pager li),
.table-pagination :deep(.el-pagination.is-background button) {
  color: #bdeef5;
  background: rgba(0, 31, 44, .9);
  border: 1px solid rgba(0, 214, 255, .22);
}
.table-pagination :deep(.el-pagination.is-background .el-pager li.is-active) {
  color: #04121d;
  background: #25f5d0;
}
.obsidian-actions .el-input { width: min(520px, 40vw); }
.obsidian-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(180px, 1fr));
  gap: 12px;
  padding: 16px;
}
.obsidian-summary div, .obsidian-counts article, .obsidian-result {
  border: 1px solid rgba(0, 214, 255, .18);
  background: rgba(0, 0, 0, .16);
}
.obsidian-summary div {
  min-height: 86px;
  padding: 14px;
}
.obsidian-summary span, .obsidian-counts span { display: block; color: #7faeba; margin-bottom: 8px; }
.obsidian-summary strong {
  display: block;
  color: #ffffff;
  font-size: 15px;
  word-break: break-all;
}
.obsidian-counts {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  gap: 12px;
  padding: 0 16px 16px;
}
.obsidian-counts article {
  padding: 14px;
}
.obsidian-counts strong {
  color: #25f5d0;
  font-size: 30px;
  line-height: 1;
}
.obsidian-result {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 0 16px 16px;
  padding: 14px;
  color: #bdeef5;
}
.obsidian-result strong { width: 100%; color: #ffffff; }
.analysis-result { display: grid; grid-template-columns: repeat(4, max-content) 1fr; align-items: start; }
.analysis-result strong, .analysis-list { grid-column: 1 / -1; }
.analysis-list {
  display: grid;
  gap: 8px;
  width: 100%;
}
.analysis-list article {
  display: grid;
  grid-template-columns: 60px minmax(220px, 1fr) minmax(220px, 1.2fr);
  gap: 10px;
  align-items: center;
  padding: 10px;
  border: 1px solid rgba(0, 214, 255, .16);
  background: rgba(0, 0, 0, .18);
}
.analysis-list article.failed { border-color: rgba(255, 112, 112, .35); }
.analysis-list b { color: #25f5d0; }
.analysis-list article.failed b { color: #ff8f8f; }
.analysis-list span { word-break: break-all; }
.analysis-list small { color: #8ebdc8; }
.obsidian-analysis-board {
  margin: 16px;
  padding: 18px;
  border: 1px solid rgba(0, 214, 255, .2);
  background: rgba(0, 22, 34, .52);
}
.analysis-board-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  margin-bottom: 16px;
}
.analysis-board-head strong { display: block; color: #ffffff; font-size: 18px; margin-bottom: 6px; }
.analysis-board-head span { color: #8ebdc8; }
.analysis-board-head .el-input { width: 280px; }
.analysis-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.analysis-card {
  min-height: 138px;
  padding: 12px;
  border: 1px solid rgba(0, 214, 255, .28);
  background: linear-gradient(180deg, rgba(0, 83, 101, .38), rgba(0, 28, 38, .72));
  cursor: pointer;
}
.analysis-card:hover { border-color: rgba(37, 245, 208, .78); background: linear-gradient(180deg, rgba(0, 104, 120, .45), rgba(0, 35, 45, .78)); }
.analysis-card-top {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.analysis-card-top strong {
  color: #ffffff;
  font-size: 16px;
  line-height: 1.35;
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
}
.analysis-meta {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  margin-top: 6px;
  color: #bfeaf1;
  font-size: 13px;
}
.analysis-meta span { color: #7faeba; }
.analysis-meta b {
  color: #dffaff;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.analysis-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
  min-height: 23px;
}
.analysis-tags b {
  color: #0a2830;
  background: #25f5d0;
  padding: 2px 7px;
  font-size: 12px;
}
.analysis-tags em {
  color: #78aab5;
  font-style: normal;
  font-size: 12px;
}
.analysis-detail-link {
  margin-top: 10px;
  padding: 5px 10px;
  color: #25f5d0;
  border: 1px solid rgba(37, 245, 208, .38);
  background: rgba(37, 245, 208, .08);
  cursor: pointer;
}
.analysis-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.analysis-pagination :deep(.el-pagination.is-background .el-pager li),
.analysis-pagination :deep(.el-pagination.is-background button) {
  color: #bdeef5;
  background: rgba(0, 31, 44, .9);
  border: 1px solid rgba(0, 214, 255, .22);
}
.analysis-pagination :deep(.el-pagination.is-background .el-pager li.is-active) {
  color: #04121d;
  background: #25f5d0;
}
.analysis-detail-dialog :deep(.el-dialog) {
  background: linear-gradient(180deg, #062130, #03131d);
  border: 1px solid rgba(0, 214, 255, .38);
  box-shadow: 0 28px 80px rgba(0, 0, 0, .55), inset 0 0 32px rgba(0, 214, 255, .06);
}
.analysis-detail-dialog :deep(.el-dialog__header) {
  padding: 22px 24px 12px;
  border-bottom: 1px solid rgba(0, 214, 255, .18);
}
.analysis-detail-dialog :deep(.el-dialog__body) {
  padding: 22px 24px 24px;
  background: transparent;
}
.analysis-detail-dialog :deep(.el-dialog__title) { color: #ffffff; }
.analysis-detail-dialog :deep(.el-dialog__headerbtn .el-dialog__close) { color: #8ed7e7; }
.analysis-detail-dialog :deep(.el-dialog__headerbtn:hover .el-dialog__close) { color: #25f5d0; }
:global(.analysis-detail-dialog),
:global(.analysis-detail-dialog.el-dialog),
:global(.analysis-detail-dialog .el-dialog) {
  background: linear-gradient(180deg, #062130, #03131d) !important;
  border: 1px solid rgba(0, 214, 255, .38) !important;
  box-shadow: 0 28px 80px rgba(0, 0, 0, .55), inset 0 0 32px rgba(0, 214, 255, .06) !important;
}
:global(.analysis-detail-dialog .el-dialog__header),
:global(.analysis-detail-dialog.el-dialog .el-dialog__header) {
  background: transparent !important;
  border-bottom: 1px solid rgba(0, 214, 255, .18);
}
:global(.analysis-detail-dialog .el-dialog__body),
:global(.analysis-detail-dialog.el-dialog .el-dialog__body) {
  background: transparent !important;
}
:global(.analysis-detail-dialog .el-dialog__title),
:global(.analysis-detail-dialog.el-dialog .el-dialog__title) {
  color: #ffffff !important;
}
:global(.analysis-detail-dialog .el-dialog__close),
:global(.analysis-detail-dialog.el-dialog .el-dialog__close) {
  color: #8ed7e7 !important;
}
.analysis-detail {
  display: grid;
  gap: 16px;
  color: #dffaff;
}
.analysis-detail header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(0, 214, 255, .22);
}
.analysis-detail header strong { color: #ffffff; font-size: 24px; line-height: 1.3; }
.analysis-detail header span { color: #25f5d0; }
.detail-meta {
  display: grid;
  grid-template-columns: 86px 1fr;
  gap: 12px;
  align-items: start;
}
.detail-meta span { color: #7faeba; }
.detail-meta b { color: #ffffff; word-break: break-all; }
.analysis-detail section {
  padding: 14px;
  border: 1px solid rgba(0, 214, 255, .16);
  background: rgba(0, 32, 44, .55);
}
.analysis-detail h3 { margin: 0 0 10px; color: #25f5d0; font-size: 16px; }
.analysis-detail p { margin: 0; line-height: 1.75; white-space: pre-wrap; }
.detail-commentary { max-height: 320px; overflow: auto; }
.detail-tags { display: flex; flex-wrap: wrap; gap: 8px; }
.detail-tags b {
  padding: 4px 9px;
  color: #042028;
  background: #25f5d0;
}
.detail-tags span { color: #7faeba; }
.detail-faqs {
  display: grid;
  gap: 10px;
}
.detail-faqs article {
  padding: 12px;
  border: 1px solid rgba(37, 245, 208, .18);
  background: rgba(0, 22, 34, .52);
}
.detail-faqs strong {
  display: block;
  margin-bottom: 8px;
  color: #ffffff;
  line-height: 1.55;
}
.kb-row {
  display: grid;
  grid-template-columns: 54px 122px minmax(160px, 1.1fr) minmax(240px, 1.4fr) 88px 80px 112px 96px 92px;
  align-items: center;
  gap: 12px;
  min-width: 1180px;
  padding: 10px 16px;
  border-bottom: 1px solid rgba(0, 214, 255, .1);
}
.hidden-file-input { display: none; }
.kb-row:not(.kb-row-head):hover { background: rgba(0, 214, 255, .06); }
.kb-row-head {
  position: sticky;
  top: 0;
  z-index: 1;
  min-height: 42px;
  color: #7faeba;
  font-size: 13px;
  background: rgba(5, 28, 43, .96);
}
.thumb {
  width: 104px;
  height: 58px;
  display: block;
  object-fit: cover;
  background: rgba(0,0,0,.25);
  border: 1px solid rgba(0,214,255,.3);
}
.thumb.placeholder { display: grid; place-items: center; color: #25f5d0; font-style: normal; }
.muted { color: #7faeba; }
.strong { color: #fff; font-weight: 700; }
.file-name { color: #b6dce3; overflow-wrap: anywhere; }
.tag {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 8px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid transparent;
}
.tag.blue { color: #69c4ff; background: rgba(48, 139, 255, .12); border-color: rgba(48, 139, 255, .22); }
.tag.amber { color: #ffd08a; background: rgba(255, 164, 46, .12); border-color: rgba(255, 164, 46, .22); }
.tag.green { color: #8effc9; background: rgba(45, 214, 135, .12); border-color: rgba(45, 214, 135, .22); }
.tag.gray { color: #93a5ac; background: rgba(147, 165, 172, .12); border-color: rgba(147, 165, 172, .2); }
.empty-line {
  padding: 34px;
  text-align: center;
  color: #7faeba;
}
.qa-bulkbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  color: #9ed9e3;
  border-top: 1px solid rgba(0, 214, 255, .12);
  border-bottom: 1px solid rgba(0, 214, 255, .12);
  background: rgba(0, 0, 0, .12);
  flex-wrap: wrap;
}
.qa-bulkbar :deep(.el-checkbox__label) { color: #d9fbff; }
.qa-list { display: grid; gap: 12px; padding: 16px; }
.qa-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 16px;
  padding: 16px;
  background: rgba(1, 14, 24, .62);
  border: 1px solid rgba(0, 214, 255, .2);
}
.qa-card.selected {
  border-color: rgba(37, 240, 207, .64);
  background: rgba(37, 240, 207, .08);
}
.qa-select {
  align-self: start;
  padding-top: 3px;
}
.qa-card.disabled { opacity: .62; }
.qa-question { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.qa-question strong { color: #fff; font-size: 17px; }
.qa-card p { color: #c8eef5; line-height: 1.7; white-space: pre-wrap; }
.status-pill {
  padding: 3px 8px;
  color: #04121d;
  background: #25f5d0;
  font-size: 12px;
}
.status-pill.off { color: #c4d4d9; background: rgba(147, 165, 172, .18); }
.keyword-line { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; color: #7faeba; }
.keyword-line b {
  padding: 4px 8px;
  color: #8ddcff;
  background: rgba(0, 214, 255, .1);
  border: 1px solid rgba(0, 214, 255, .18);
  font-weight: 500;
}
.qa-actions { display: flex; align-items: center; gap: 8px; }
.fallback-testbar {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 130px auto minmax(260px, 1fr);
  gap: 10px;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid rgba(0, 214, 255, .14);
  background: rgba(0, 214, 255, .035);
}
.fallback-test-result {
  min-height: 34px;
  padding: 7px 10px;
  color: #8effc9;
  border: 1px solid rgba(37, 245, 208, .25);
  background: rgba(37, 245, 208, .08);
  line-height: 1.4;
}
.fallback-test-result.missed { color: #ffcf91; border-color: rgba(255, 175, 76, .28); background: rgba(255, 175, 76, .08); }
.fallback-summary { display: flex; gap: 24px; padding: 12px 16px 0; color: #7faeba; }
.fallback-list { display: grid; gap: 12px; padding: 16px; }
.fallback-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  padding: 16px;
  border: 1px solid rgba(0, 214, 255, .2);
  background: rgba(1, 14, 24, .62);
}
.fallback-card.disabled { opacity: .62; }
.fallback-card-title { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; }
.fallback-card-title strong { color: #fff; font-size: 17px; }
.fallback-card p { margin: 12px 0 0; color: #c8eef5; line-height: 1.7; white-space: pre-wrap; }
.fallback-metrics { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.fallback-metrics span {
  padding: 4px 8px;
  color: #b7dce5;
  border: 1px solid rgba(127, 174, 186, .18);
  background: rgba(127, 174, 186, .08);
  font-size: 12px;
}
.fallback-metrics.rule-performance span {
  color: #8cf6de;
  border-color: rgba(37, 245, 208, .22);
  background: rgba(37, 245, 208, .07);
}
.fallback-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 18px; }
.fallback-form-grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.fallback-form-grid :deep(.el-select), .fallback-form-grid :deep(.el-input-number) { width: 100%; }
:global(.fallback-dialog.el-dialog),
:global(.fallback-dialog .el-dialog) {
  color: #d9fbff;
  background: #071d2b;
  border: 1px solid rgba(0, 214, 255, .28);
}
:global(.fallback-dialog .el-dialog__header),
:global(.fallback-dialog .el-dialog__body),
:global(.fallback-dialog .el-dialog__footer) { background: #071d2b; }
:global(.fallback-dialog .el-dialog__title) { color: #fff; }
:global(.fallback-dialog .el-dialog__close) { color: #8ed7e7; }
@media (max-width: 980px) {
  .fallback-testbar { grid-template-columns: 1fr 130px auto; }
  .fallback-test-result { grid-column: 1 / -1; }
  .fallback-form-grid, .fallback-form-grid.three { grid-template-columns: 1fr; }
}
.screen-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}
.screen-card {
  overflow: hidden;
  background: rgba(2, 18, 30, .76);
  border: 1px solid rgba(0,214,255,.26);
  box-shadow: 0 0 28px rgba(0, 214, 255, .06) inset;
}
.screen-card.offline { opacity: .62; }
.screen-card-preview {
  position: relative;
  aspect-ratio: 16 / 9;
  background:
    linear-gradient(135deg, rgba(0,214,255,.08), rgba(37,245,208,.05)),
    repeating-linear-gradient(0deg, transparent 0 31px, rgba(0,214,255,.07) 32px),
    repeating-linear-gradient(90deg, transparent 0 31px, rgba(0,214,255,.07) 32px);
}
.screen-card-preview img { width: 100%; height: 100%; object-fit: cover; display: block; }
.screen-card-empty { height: 100%; display: grid; place-items: center; color: #6f97a3; font-size: 15px; }
.screen-card-badge, .screen-card-status {
  position: absolute;
  top: 12px;
  padding: 6px 10px;
  background: rgba(0, 12, 22, .78);
  border: 1px solid rgba(0,214,255,.28);
  color: #d9fbff;
  backdrop-filter: blur(8px);
}
.screen-card-badge { left: 12px; color: #25f5d0; font-weight: 700; }
.screen-card-status { right: 12px; display: flex; align-items: center; gap: 6px; color: #ffb4b4; }
.screen-card-status.online { color: #6fffd3; }
.screen-card-status span {
  width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 10px currentColor;
}
.screen-card-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "identity actions"
    "metrics metrics";
  align-items: start;
  gap: 12px 16px;
  padding: 16px 20px 18px;
}
.screen-card-body > div:first-child { grid-area: identity; min-width: 0; }
.screen-card-body h3 {
  margin: 0 0 6px;
  overflow: hidden;
  color: #ffffff;
  font-size: 17px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.screen-card-body p { margin: 0; color: #80aeba; font-size: 13px; white-space: nowrap; }
.screen-metrics {
  grid-area: metrics;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: stretch;
  gap: 8px;
  color: #c8eef5;
  font-size: 12px;
  min-width: 0;
}
.screen-metrics > span {
  display: flex;
  align-items: center;
  min-height: 48px;
  padding: 8px 12px;
  background: rgba(0, 214, 255, .08);
  border: 1px solid rgba(0, 214, 255, .16);
  white-space: nowrap;
}
.screen-meter {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  min-height: 48px;
  padding: 8px 12px;
  background: rgba(32, 214, 142, .1);
  border: 1px solid rgba(32, 214, 142, .26);
}
.screen-meter.danger {
  background: rgba(255, 79, 79, .1);
  border-color: rgba(255, 79, 79, .36);
}
.screen-meter-head {
  margin-bottom: 5px;
}
.screen-meter-head span {
  display: block;
  min-height: 0;
  padding: 0;
  background: transparent;
  border: 0;
}
.screen-meter-track {
  height: 5px;
  overflow: hidden;
  background: rgba(255, 255, 255, .12);
}
.screen-meter-track i {
  display: block;
  height: 100%;
  max-width: 100%;
  background: linear-gradient(90deg, #26d97b, #6fffd3);
}
.screen-meter.danger .screen-meter-track i {
  background: linear-gradient(90deg, #ff5959, #ff9d7a);
}
.screen-card-actions {
  grid-area: actions;
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  flex-shrink: 0;
  white-space: nowrap;
}
.special-layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 18px; }
.special-list { display: grid; gap: 10px; }
.special-item, .voice-card {
  text-align: left; padding: 14px; color: #d9fbff; background: rgba(0,0,0,.18);
  border: 1px solid rgba(0,214,255,.22); cursor: pointer;
}
.special-item span { display: block; font-weight: 700; margin-bottom: 5px; }
.special-item small { color: #80aeba; }
.special-item.active, .special-item:hover, .voice-card.active, .voice-card:hover {
  border-color: #25f5d0; background: rgba(37,245,208,.08);
}
.special-editor { display: grid; gap: 16px; }
.assignment-table {
  border: 1px solid rgba(0,214,255,.16);
}
.assignment-head, .assignment-row {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  align-items: start;
  gap: 14px;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(0,214,255,.12);
}
.assignment-head { color: #7faeba; background: rgba(0,0,0,.18); }
.assignment-files {
  display: grid;
  gap: 10px;
  min-width: 0;
}
.assignment-file-list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 10px 12px 10px 34px;
  border: 1px solid rgba(0,214,255,.12);
  background: rgba(2, 18, 31, .58);
  color: #d9fbff;
}
.assignment-file-list li {
  padding: 4px 0;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.assignment-file-list li::marker {
  color: #25f5d0;
  font-weight: 700;
}
.assignment-empty {
  color: #6d98a5;
  font-size: 13px;
}
.voice-panel { max-width: 1120px; }
.voice-provider-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.voice-provider-button {
  min-width: 132px;
  height: 38px;
  padding: 0 16px;
  color: #9ed9e3;
  background: rgba(0, 0, 0, .18);
  border: 1px solid rgba(0,214,255,.22);
  cursor: pointer;
}
.voice-provider-button.active,
.voice-provider-button:hover {
  color: #04121d;
  background: #25f5d0;
  border-color: #25f5d0;
}
.voice-provider-button span {
  font-weight: 700;
}
.voice-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.voice-card span { display: block; color: #25f5d0; font-weight: 700; margin-bottom: 8px; }
.voice-card strong { display: block; font-size: 22px; margin-bottom: 6px; }
.voice-card small { color: #80aeba; overflow-wrap: anywhere; }
.voice-test { margin-top: 4px; }
.audio-player { width: 100%; margin-top: 14px; }
.qa-form { display: grid; gap: 14px; }
:global(.screen-settings-dialog) {
  background: rgba(3, 22, 35, .98);
  border: 1px solid rgba(0, 214, 255, .32);
  box-shadow: 0 24px 70px rgba(0, 0, 0, .48), 0 0 36px rgba(0, 214, 255, .1) inset;
}
:global(.screen-settings-dialog .el-dialog__header) {
  padding: 22px 24px 12px;
  margin: 0;
}
:global(.screen-settings-dialog .el-dialog__title) {
  color: #d9fbff;
  font-size: 22px;
  font-weight: 800;
}
:global(.screen-settings-dialog .el-dialog__headerbtn .el-dialog__close) {
  color: #8ddcff;
}
:global(.screen-settings-dialog .el-dialog__body) {
  padding: 14px 24px 8px;
}
:global(.screen-settings-dialog .el-dialog__footer) {
  padding: 14px 24px 22px;
}
:global(.screen-settings-dialog .el-form-item__label) {
  color: #8ddcff;
}
:global(.screen-settings-dialog .el-input__wrapper),
:global(.screen-settings-dialog .el-input-number__decrease),
:global(.screen-settings-dialog .el-input-number__increase) {
  background: rgba(8, 31, 48, .95);
  border-color: rgba(0, 214, 255, .26);
  box-shadow: 0 0 0 1px rgba(0,214,255,.22) inset;
}
:global(.screen-settings-dialog .el-input__inner) {
  color: #d9fbff;
}
:global(.screen-settings-dialog .el-input-number__decrease),
:global(.screen-settings-dialog .el-input-number__increase) {
  color: #d9fbff;
}
:global(.screen-settings-dialog .el-input-number__decrease:hover),
:global(.screen-settings-dialog .el-input-number__increase:hover) {
  color: #25f5d0;
}
:global(.screen-settings-dialog .el-button:not(.el-button--primary)) {
  background: rgba(5, 20, 32, .92);
  border-color: rgba(141, 220, 255, .3);
  color: #d9fbff;
}
:deep(.el-input__wrapper),
:deep(.el-select__wrapper),
:deep(.el-textarea__inner) {
  background: rgba(4, 20, 33, .9);
  border-radius: 2px;
  box-shadow: 0 0 0 1px rgba(0,214,255,.16) inset;
}
:deep(.el-input__inner),
:deep(.el-select__placeholder),
:deep(.el-textarea__inner) {
  color: #d9fbff;
}
:deep(.el-form-item__label) { color: #9ed9e3; }
@media (max-width: 1100px) {
  .stats-grid, .voice-grid, .web-candidate-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .special-layout { grid-template-columns: 1fr; }
  .simulator-target { width: 100%; }
  .web-candidate-toolbar { grid-template-columns: 1fr; }
  .web-candidate-card { flex-direction: column; }
  .web-candidate-actions { width: 100%; flex-direction: row; }
  .chat-workspace { grid-template-columns: 190px minmax(420px, 1fr); }
  .chat-diagnostic-panel { display: none; }
  .chat-composer { grid-template-columns: 1fr auto; }
  .chat-composer .simulator-target { grid-column: 1 / -1; }
}
</style>

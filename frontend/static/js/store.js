async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || "请求失败");
  return data;
}

const STATUS_IDLE = "idle";
const STATUS_RUNNING = "running";
const STATUS_SUCCESS = "success";
const STATUS_FAILED = "failed";
const STATUS_DIRTY = "dirty";

const { reactive } = Vue;
const store = reactive({
  projects: [], project: null, episodes: [], speakers: [], currentEpisode: "", currentSpeakerId: null,
  clips: [], reviewItems: [], page: 1, total: 0, totalClips: 0, pages: 1, keyword: "", selectedFilter: "", threshold: 40,
  references: [], trainingSegments: [], analyses: {}, prototypeStatus: null, exportSummary: null, batchSummary: null,
  exportDialog: false, batchExportDialog: false, exportMenuOpen: false, toast: "", newProject: false, addEpisodeDialog: false, newSpeaker: false,
  exportLanguage: "ZH",
  skipShortClips: false,
  form: { name: "", video: "", subtitle: "", episode: "", speaker: "" }, activeClip: null,
  clipEdit: { id: null, trim_start: 0, trim_end: 0 }, playStopTimer: null,
  get pid() { return this.project ? this.project.folder : ""; },
  get speaker() { return this.speakers.find(item => item.id === this.currentSpeakerId) || null; },
  get currentAnalysis() {
    if (!this.currentSpeakerId || !this.currentEpisode) return null;
    return this.analyses[`${this.currentSpeakerId}:${this.currentEpisode}`] || null;
  },
  get currentAnalysisDirty() {
    const a = this.currentAnalysis;
    if (!a || a.status === STATUS_IDLE) return false;
    if (a.status === STATUS_DIRTY) return true;
    if (this.prototypeStatus && this.prototypeStatus.status === STATUS_DIRTY) return true;
    if (a.status === STATUS_SUCCESS && a.result && a.result.threshold !== this.threshold / 100) return true;
    return false;
  },
  tell(message) { this.toast = message; setTimeout(() => { if (this.toast === message) this.toast = ""; }, 3500); },
  async loadProjects() { this.projects = await api("/api/projects"); },
  async createProject() {
    const item = await api("/api/projects", { method: "POST", body: JSON.stringify({ name: this.form.name }) });
    this.form.name = ""; this.newProject = false; await this.open(item.folder);
  },
  async deleteProject(folder) {
    if (!confirm(`确认删除项目"${folder}"？该操作不可恢复。`)) return;
    await api(`/api/projects/${encodeURIComponent(folder)}`, { method: "DELETE" });
    await this.loadProjects();
    this.tell("项目已删除");
  },
  async open(folder) {
    this.project = await api(`/api/projects/${encodeURIComponent(folder)}/open`, { method: "POST" });
    this.episodes = this.project.episodes || []; this.currentEpisode = this.episodes[0]?.name || ""; this.currentSpeakerId = null;
    this.prototypeStatus = null;
    await this.refresh();
  },
  async refresh() {
    if (!this.pid) return;
    const q = new URLSearchParams({ project: this.pid });
    if (this.currentSpeakerId) q.set("speaker_id", this.currentSpeakerId);
    const side = await api(`/api/sidebar?${q}`);
    this.speakers = side.speakers;
    const epStats = side.episodes || [];
    for (const ep of this.episodes) {
      const stat = epStats.find(s => s.episode === ep.name);
      ep.analysis_status = stat ? stat.analysis_status : STATUS_IDLE;
      ep.analyzed = stat ? !!stat.analyzed : false;
    }
    if (this.currentSpeakerId) { await this.loadReferences(); await this.loadTrainingSegments(); }
    await this.loadClips();
  },
  async addEpisode() {
    const data = await api(`/api/projects/${encodeURIComponent(this.pid)}/episodes`, { method: "POST", body: JSON.stringify({ video_path: this.form.video, subtitle_path: this.form.subtitle, episode_name: this.form.episode }) });
    this.episodes.push({ name: data.episode, video: this.form.video, subtitle: this.form.subtitle }); this.currentEpisode = data.episode;
    this.form.video = this.form.subtitle = this.form.episode = ""; this.addEpisodeDialog = false; this.tell(`已解析 ${data.clip_count} 条字幕片段`); await this.refresh();
  },
  async createSpeaker() {
    const item = await api("/api/speakers", { method: "POST", body: JSON.stringify({ project: this.pid, name: this.form.speaker }) });
    this.form.speaker = ""; this.newSpeaker = false; await this.refresh(); this.chooseSpeaker(item.id);
  },
  async pickFile(type) {
    const result = await api(`/api/pick-file?type=${encodeURIComponent(type)}`);
    if (!result.path) return;
    if (type === 'video') {
      this.form.video = result.path;
      if (!this.form.episode && result.name) this.form.episode = result.name;
    } else if (type === 'subtitle') {
      this.form.subtitle = result.path;
    }
  },
  async chooseSpeaker(id) { this.currentSpeakerId = id; this.page = 1; await this.refresh(); await this.loadPrototypeStatus(); await this.loadAnalysis(); },
  async switchEpisode(name) { this.currentEpisode = name; this.page = 1; await this.loadClips(); await this.loadPrototypeStatus(); await this.loadAnalysis(); await this.loadTrainingSegments(); },
  async loadReferences() { if (this.currentSpeakerId) this.references = await api(`/api/speakers/${this.currentSpeakerId}/references?project=${encodeURIComponent(this.pid)}`); else this.references = []; },
  async loadTrainingSegments() {
    if (!this.currentSpeakerId || !this.currentEpisode) { this.trainingSegments = []; return; }
    this.trainingSegments = await api(`/api/speakers/${this.currentSpeakerId}/training-segments?project=${encodeURIComponent(this.pid)}&episode=${encodeURIComponent(this.currentEpisode)}`);
  },
  async generateTrainingSegments(regenerate = false) {
    if (!this.currentSpeakerId || !this.currentEpisode) return;
    if (regenerate && !confirm("重新生成当前素材的合并建议？\n\n现有待确认建议将被替换；已采用的合并段不会受影响。")) return;
    const result = await api(`/api/speakers/${this.currentSpeakerId}/training-segments/generate`, {
      method: "POST", body: JSON.stringify({ project: this.pid, episode: this.currentEpisode, min_duration: 3, max_duration: 10, max_gap: 0.35 })
    });
    await this.loadTrainingSegments();
    this.tell(result.created ? `已${regenerate ? "重新" : ""}生成 ${result.created} 个 3–10 秒合并建议` : "没有找到可安全合并为 3 秒以上的连续片段");
  },
  async updateTrainingSegmentStatus(segment, status) {
    await api(`/api/training-segments/${segment.id}/status`, { method: "PATCH", body: JSON.stringify({ project: this.pid, status }) });
    await this.loadTrainingSegments();
    await this.loadClips();
    this.tell(status === "approved" ? "已采用合并训练片段，导出时会替代组成 Clip" : status === "candidate" ? "已取消采用，Clip 已恢复独立展示" : "已忽略该合并建议");
  },
  async loadClips() {
    if (!this.pid) return;
    const q = new URLSearchParams({ project: this.pid, page: this.page, limit: 100 });
    if (this.currentEpisode) q.set("episode", this.currentEpisode); if (this.currentSpeakerId) q.set("speaker", this.currentSpeakerId);
    if (this.keyword) q.set("keyword", this.keyword); if (this.selectedFilter) q.set("selected", this.selectedFilter);
    const data = await api(`/api/review-items?${q}`);
    this.reviewItems = data.items;
    this.clips = data.items.flatMap(item => item.type === "segment" ? item.clips : [item.clip]);
    this.total = data.total; this.totalClips = data.total_clips; this.pages = data.total_pages;
    if (this.page > this.pages && this.pages > 0) { this.page = this.pages; return this.loadClips(); }
  },
  goPage(page) {
    page = Math.max(1, Math.min(this.pages, page));
    if (page === this.page) return;
    this.page = page; this.loadClips();
  },
  async toggleClip(clip) {
    const selected_speaker_id = clip.selected_speaker_id === this.currentSpeakerId ? null : this.currentSpeakerId;
    if (!this.currentSpeakerId) return this.tell("请先选择一个角色");
    await api(`/api/clips/${clip.id}/selection`, { method: "PATCH", body: JSON.stringify({ project: this.pid, selected_speaker_id }) });
    clip.selected_speaker_id = selected_speaker_id; await this.refresh();
  },
  async addReference(clip) {
    if (!this.currentSpeakerId) return this.tell("请先选择一个角色");
    await api(`/api/speakers/${this.currentSpeakerId}/references`, { method: "POST", body: JSON.stringify({ project: this.pid, clip_id: clip.id }) });
    await this.loadReferences(); await this.refresh(); await this.loadPrototypeStatus();
    this.tell("已加入参考素材");
  },
  async removeReference(id) {
    await api(`/api/speakers/${this.currentSpeakerId}/references/${id}?project=${encodeURIComponent(this.pid)}`, { method: "DELETE" });
    await this.loadReferences(); await this.loadPrototypeStatus();
  },
  async loadPrototypeStatus() {
    if (!this.currentSpeakerId) { this.prototypeStatus = null; return; }
    this.prototypeStatus = await api(`/api/speakers/${this.currentSpeakerId}/prototype/status?project=${encodeURIComponent(this.pid)}`);
  },
  async analyze() {
    if (!this.currentSpeakerId || !this.currentEpisode) return;
    const current = this.currentAnalysis;
    if (current && current.status === STATUS_SUCCESS) {
      if (!confirm("重新分析当前素材？\n\n当前素材已有的 AI 自动保留结果将重新生成。\n不会影响其它素材。\n\n继续？")) return;
    }
    const key = `${this.currentSpeakerId}:${this.currentEpisode}`;
    await api(`/api/speakers/${this.currentSpeakerId}/analyze`, { method: "POST", body: JSON.stringify({ project: this.pid, episode: this.currentEpisode, threshold: this.threshold / 100 }) });
    this.analyses[key] = { status: STATUS_RUNNING, step: "开始 AI 查找" };
    this.pollAnalysis();
  },
  async loadAnalysis() {
    if (!this.currentSpeakerId || !this.currentEpisode) return;
    const key = `${this.currentSpeakerId}:${this.currentEpisode}`;
    this.analyses[key] = await api(`/api/speakers/${this.currentSpeakerId}/analyze/status?project=${encodeURIComponent(this.pid)}&episode=${encodeURIComponent(this.currentEpisode)}`);
  },
  async pollAnalysis() {
    if (!this.currentSpeakerId || !this.currentEpisode) return;
    const key = `${this.currentSpeakerId}:${this.currentEpisode}`;
    const status = await api(`/api/speakers/${this.currentSpeakerId}/analyze/status?project=${encodeURIComponent(this.pid)}&episode=${encodeURIComponent(this.currentEpisode)}`);
    this.analyses[key] = status;
    if (status.status === STATUS_RUNNING) setTimeout(() => this.pollAnalysis(), 700);
    else if (status.status === STATUS_SUCCESS) {
      await this.refresh(); await this.loadPrototypeStatus();
      this.tell(`AI 查找完成：在"${status.result?.episode || this.currentEpisode}"中保留 ${status.result?.selected || 0} 条候选`);
    } else if (status.status === STATUS_FAILED) {
      await this.refresh(); await this.loadPrototypeStatus();
      this.tell(status.error || "AI 查找失败");
    }
  },
  async showExportDialog() {
    if (!this.currentSpeakerId) return this.tell("请先选择一个角色");
    this.exportSummary = await api(`/api/speakers/${this.currentSpeakerId}/export/summary?project=${encodeURIComponent(this.pid)}`);
    this.exportDialog = true;
  },
  async doExport(requireSuccess) {
    this.exportDialog = false;
    const result = await api(`/api/speakers/${this.currentSpeakerId}/export`, { method: "POST", body: JSON.stringify({ project: this.pid, require_success: requireSuccess, language: this.exportLanguage, skip_short: this.skipShortClips }) });
    let msg = `已导出 ${result.wav_count} 条训练音频到 ${result.output_dir}`;
    if (result.error_count) msg += `\n${result.error_count} 条失败`;
    if (result.skipped_short) msg += `\n已过滤 ${result.skipped_short} 条短片段（<1s）`;
    if (result.skipped_episodes && result.skipped_episodes.length) msg += `\n已跳过：${result.skipped_episodes.join('、')}`;
    this.tell(msg);
  },
  async showBatchExportDialog() {
    this.batchSummary = await api(`/api/projects/${encodeURIComponent(this.pid)}/export-summary`);
    this.batchExportDialog = true;
  },
  async doBatchExport(requireSuccess) {
    this.batchExportDialog = false;
    const result = await api(`/api/projects/${encodeURIComponent(this.pid)}/export-all`, { method: "POST", body: JSON.stringify({ require_success: requireSuccess, language: this.exportLanguage, skip_short: this.skipShortClips }) });
    const errCount = result.speakers.reduce((s, sp) => s + (sp.error_count || 0), 0);
    const shortCount = result.speakers.reduce((s, sp) => s + (sp.skipped_short || 0), 0);
    let msg = `批量导出完成：${result.total_speakers} 个角色、${result.total_wavs} 条音频`;
    if (errCount) msg += `\n${errCount} 条失败`;
    if (shortCount) msg += `\n已过滤 ${shortCount} 条短片段（<1s）`;
    this.tell(msg);
  },
  async deleteSpeaker() { if (confirm("删除该角色及其参考素材？")) { await api(`/api/speakers/${this.currentSpeakerId}?project=${encodeURIComponent(this.pid)}`, { method: "DELETE" }); this.currentSpeakerId = null; this.prototypeStatus = null; await this.refresh(); } },
  playRange(start, end) {
    const video = document.querySelector("video");
    if (!video) return;
    if (this.playStopTimer) clearInterval(this.playStopTimer);
    video.currentTime = start;
    video.play();
    this.playStopTimer = setInterval(() => {
      if (video.currentTime >= end) { video.pause(); clearInterval(this.playStopTimer); this.playStopTimer = null; }
    }, 80);
  },
  play(clip) {
    const editing = this.clipEdit.id === clip.id ? this.clipEdit : null;
    const effStart = editing ? (clip.start + editing.trim_start) : (clip.effective_start || clip.start);
    const effEnd = editing ? (clip.end + editing.trim_end) : (clip.effective_end || clip.end);
    this.activeClip = clip.id;
    this.playRange(effStart, effEnd);
  },
  playTrainingSegment(segment) { this.activeClip = null; this.playRange(segment.start, segment.end); },
  startTrimEdit(clip) {
    this.clipEdit = { id: clip.id, trim_start: clip.trim_start || 0, trim_end: clip.trim_end || 0 };
  },
  cancelTrimEdit() {
    this.clipEdit = { id: null, trim_start: 0, trim_end: 0 };
  },
  shiftTrimStart(step) {
    this.clipEdit.trim_start = Math.max(-2, Math.min(2, +(this.clipEdit.trim_start + step).toFixed(2)));
  },
  shiftTrimEnd(step) {
    this.clipEdit.trim_end = Math.max(-2, Math.min(2, +(this.clipEdit.trim_end + step).toFixed(2)));
  },
  playPreview() {
    const e = this.clipEdit;
    if (!e.id) return;
    const clip = this.clips.find(c => c.id === e.id);
    if (clip) this.play(clip);
  },
  async saveTrim() {
    const e = this.clipEdit;
    if (!e.id || !this.pid) return;
    await api(`/api/clips/${e.id}/trim`, { method: "PATCH", body: JSON.stringify({ project: this.pid, trim_start: e.trim_start, trim_end: e.trim_end }) });
    const clip = this.clips.find(c => c.id === e.id);
    if (clip) {
      clip.trim_start = e.trim_start;
      clip.trim_end = e.trim_end;
      clip.effective_start = clip.start + e.trim_start;
      clip.effective_end = clip.end + e.trim_end;
      clip.duration = clip.effective_end - clip.effective_start;
    }
    this.clipEdit = { id: null, trim_start: 0, trim_end: 0 };
    this.tell("Trim 已保存");
    await this.loadPrototypeStatus();
    await this.loadReferences();
    await this.loadTrainingSegments();
    await this.loadClips();
  },
  async resetTrim() {
    this.clipEdit.trim_start = 0;
    this.clipEdit.trim_end = 0;
    await this.saveTrim();
  },
});

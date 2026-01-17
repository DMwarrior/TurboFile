
        const socket = io();
        let currentTransferId = null;
        let latestTransferredBytes = 0;
        let selectedSourceFiles = [];
        let selectedTargetFiles = [];
        let currentTransferDirection = 'ltr';
        let currentTransferMode = 'copy';
        let currentSourcePath = '';
        let currentTargetPath = '';
        let transferContext = null;


        let windowsDrivesSource = [];
        let windowsDrivesTarget = [];


        const BROWSE_PAGE_SIZE = 400;
        const BROWSE_PAGE_SIZE_MAX = 2000;
        const browseState = {
            source: { path: null, offset: 0, total: 0, hasMore: false, loading: false, controller: null, loadedCount: 0, requestToken: '', fullItems: [], fullOffset: 0, fullHasMore: false, loadingAll: false, virtualOffset: 0, rowHeight: 0, lastScrollTop: 0, jumpTimer: null, pendingJump: null },
            target: { path: null, offset: 0, total: 0, hasMore: false, loading: false, controller: null, loadedCount: 0, requestToken: '', fullItems: [], fullOffset: 0, fullHasMore: false, loadingAll: false, virtualOffset: 0, rowHeight: 0, lastScrollTop: 0, jumpTimer: null, pendingJump: null }
        };


        const lastSelectedIndex = { source: null, target: null };
        let lastActivePanel = 'source';




        let transferClipboard = null; // { mode: 'copy'|'move', sourcePanel: 'source'|'target', sourceServer: string, files: Array<{path,name,is_directory}>, ts: number }
        let transferRefreshOverride = null; // { refreshSource: boolean, refreshTarget: boolean } | null


        const uiDialogState = { instance: null, resolve: null, type: 'alert', bound: false };

        const DRAG_TRANSFER_TYPE = 'application/x-turbofile-transfer';
        let dragTransferPayload = null;
        const DRAG_GHOST_PIXEL = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
        let dragGhostImage = null;

        function getDragGhostImage() {
            if (!dragGhostImage) {
                dragGhostImage = new Image();
                dragGhostImage.src = DRAG_GHOST_PIXEL;
            }
            return dragGhostImage;
        }

        function _getUiDialogElements() {
            return {
                modal: document.getElementById('uiDialogModal'),
                title: document.getElementById('uiDialogTitle'),
                message: document.getElementById('uiDialogMessage'),
                list: document.getElementById('uiDialogList'),
                warning: document.getElementById('uiDialogWarning'),
                warningText: document.querySelector('#uiDialogWarning span'),
                inputRow: document.getElementById('uiDialogInputRow'),
                input: document.getElementById('uiDialogInput'),
                confirmBtn: document.getElementById('uiDialogConfirm'),
                cancelBtn: document.getElementById('uiDialogCancel')
            };
        }

        function _resolveUiDialog(result) {
            if (!uiDialogState.resolve) return;
            const resolve = uiDialogState.resolve;
            uiDialogState.resolve = null;
            resolve(result);
        }

        function _ensureUiDialog() {
            if (uiDialogState.instance) return uiDialogState.instance;
            const els = _getUiDialogElements();
            if (!els.modal || typeof bootstrap === 'undefined' || !bootstrap.Modal) return null;

            uiDialogState.instance = bootstrap.Modal.getInstance(els.modal) || new bootstrap.Modal(els.modal, {
                backdrop: true,
                keyboard: true,
                focus: true
            });

            if (!uiDialogState.bound) {
                uiDialogState.bound = true;

                if (els.confirmBtn) {
                    els.confirmBtn.addEventListener('click', () => {
                        if (uiDialogState.type === 'prompt') {
                            _resolveUiDialog(els.input ? els.input.value : '');
                        } else if (uiDialogState.type === 'confirm') {
                            _resolveUiDialog(true);
                        } else {
                            _resolveUiDialog(undefined);
                        }
                        uiDialogState.instance.hide();
                    });
                }

                if (els.cancelBtn) {
                    els.cancelBtn.addEventListener('click', () => {
                        if (uiDialogState.type === 'prompt') {
                            _resolveUiDialog(null);
                        } else if (uiDialogState.type === 'confirm') {
                            _resolveUiDialog(false);
                        } else {
                            _resolveUiDialog(undefined);
                        }
                        uiDialogState.instance.hide();
                    });
                }

                if (els.input) {
                    els.input.addEventListener('keydown', (event) => {
                        if (event.key === 'Enter') {
                            event.preventDefault();
                            _resolveUiDialog(els.input.value);
                            uiDialogState.instance.hide();
                        }
                    });
                }

                els.modal.addEventListener('hidden.bs.modal', () => {
                    if (!uiDialogState.resolve) return;
                    if (uiDialogState.type === 'prompt') {
                        _resolveUiDialog(null);
                    } else if (uiDialogState.type === 'confirm') {
                        _resolveUiDialog(false);
                    } else {
                        _resolveUiDialog(undefined);
                    }
                });

                els.modal.addEventListener('shown.bs.modal', () => {
                    if (uiDialogState.type === 'prompt' && els.input) {
                        els.input.focus();
                        els.input.select();
                    } else if (els.confirmBtn) {
                        els.confirmBtn.focus();
                    }
                });
            }

            return uiDialogState.instance;
        }

        function showUiDialog(options = {}) {
            const modal = _ensureUiDialog();
            if (!modal) {
                if (options.type === 'confirm') {
                    return Promise.resolve(window.confirm(options.message || ''));
                }
                if (options.type === 'prompt') {
                    return Promise.resolve(window.prompt(options.message || '', options.defaultValue || ''));
                }
                window.alert(options.message || '');
                return Promise.resolve(undefined);
            }

            const els = _getUiDialogElements();
            uiDialogState.type = options.type || 'alert';

            if (els.title) {
                els.title.textContent = options.title || '提示';
            }
            if (els.message) {
                const msg = options.message || '';
                els.message.textContent = msg;
                els.message.style.display = msg ? 'block' : 'none';
            }

            if (els.list) {
                const items = Array.isArray(options.items) ? options.items.filter(Boolean) : [];
                els.list.innerHTML = '';
                if (items.length > 0) {
                    items.forEach((item) => {
                        const li = document.createElement('li');
                        li.textContent = String(item);
                        els.list.appendChild(li);
                    });
                    els.list.style.display = 'block';
                } else {
                    els.list.style.display = 'none';
                }
            }

            if (els.warning && els.warningText) {
                const warning = options.warning || '';
                els.warningText.textContent = warning;
                els.warning.style.display = warning ? 'flex' : 'none';
            }

            if (els.inputRow && els.input) {
                if (uiDialogState.type === 'prompt') {
                    els.inputRow.style.display = 'block';
                    els.input.value = options.defaultValue || '';
                    els.input.placeholder = options.placeholder || '';
                } else {
                    els.inputRow.style.display = 'none';
                    els.input.value = '';
                    els.input.placeholder = '';
                }
            }

            if (els.confirmBtn) {
                els.confirmBtn.textContent = options.confirmText || '确定';
                els.confirmBtn.classList.remove('btn-danger', 'btn-primary');
                els.confirmBtn.classList.add(options.danger ? 'btn-danger' : 'btn-primary');
            }
            if (els.cancelBtn) {
                els.cancelBtn.textContent = options.cancelText || '取消';
                els.cancelBtn.style.display = uiDialogState.type === 'alert' ? 'none' : 'inline-block';
            }

            return new Promise((resolve) => {
                uiDialogState.resolve = resolve;
                modal.show();
            });
        }

        function showAlertDialog(message, options = {}) {
            return showUiDialog({ ...options, type: 'alert', message });
        }

        function showConfirmDialog(message, options = {}) {
            return showUiDialog({ ...options, type: 'confirm', message });
        }

        function showPromptDialog(message, options = {}) {
            return showUiDialog({ ...options, type: 'prompt', message });
        }

        function _cloneTransferFiles(files) {
            return (files || []).map(f => ({
                path: f.path,
                name: f.name,
                is_directory: !!f.is_directory
            }));
        }

        function _getCommonParentPathFromFiles(files) {
            const parents = new Set();
            (files || []).forEach(f => {
                if (!f || !f.path) return;
                try {
                    parents.add(getParentPath(f.path));
                } catch (_) {}
            });
            if (parents.size === 1) {
                return Array.from(parents)[0];
            }
            return '';
        }

        function _joinPath(basePath, name) {
            if (!basePath) return name || '';
            const safeBase = String(basePath).replace(/\\/g, '/').replace(/\/+$/, '');
            const safeName = String(name || '').replace(/^\/+/, '');
            return safeBase ? `${safeBase}/${safeName}` : safeName;
        }

        function _getBaseName(path) {
            const normalized = String(path || '').replace(/\\/g, '/');
            const parts = normalized.split('/');
            return parts.length ? parts[parts.length - 1] : '';
        }

        function cacheTransferContext(sourceServer, targetServer, sourcePath, targetPath, files, mode) {
            transferContext = {
                sourceServer: sourceServer || '',
                targetServer: targetServer || '',
                sourcePath: sourcePath || _getCommonParentPathFromFiles(files),
                targetPath: targetPath || '',
                files: _cloneTransferFiles(files || []),
                mode: mode || 'copy'
            };
        }

        function clearTransferContext() {
            transferContext = null;
        }

        function _buildOptimisticItems(targetPath, files) {
            const ts = new Date().toISOString().slice(0, 19).replace('T', ' ');
            return (files || []).map(f => ({
                name: f.name,
                path: _joinPath(targetPath, f.name),
                is_directory: !!f.is_directory,
                size: 0,
                modified: ts
            }));
        }

        function _insertOptimisticRows(isSource, items) {
            const containerId = isSource ? 'sourceFileBrowser' : 'targetFileBrowser';
            const container = document.getElementById(containerId);
            if (!container) return;

            const nodes = Array.from(container.children);
            const parentRow = nodes.find(node => node.classList && node.classList.contains('file-item') && !node.classList.contains('selectable'));
            const anchor = parentRow ? parentRow.nextSibling : (nodes[0] || null);

            const state = isSource ? browseState.source : browseState.target;
            const baseIndex = state.loadedCount || 0;
            const sorted = sortFilesWinSCPStyle([...items]);

            sorted.forEach((file, idx) => {
                const icon = file.is_directory ? 'bi-folder-fill text-warning' : 'bi-file-earmark text-info';
                const size = file.is_directory ? '' : formatFileSize(file.size || 0);
                const fileId = `file_${containerId}_optimistic_${baseIndex + idx}`;
                const fileIdx = baseIndex + idx;

                const row = document.createElement('div');
                row.className = 'file-item selectable';
                row.id = fileId;
                row.dataset.path = file.path;
                row.dataset.name = file.name;
                row.dataset.isDirectory = file.is_directory;
                row.dataset.idx = fileIdx;
                row.onmousedown = (event) => handleFileMouseDown(event, file.path, file.name, file.is_directory, fileId, isSource);
                row.ondblclick = function() { handleFileItemDblClick(row, isSource); };
                attachRowDragHandlers(row, isSource);
                attachRowDropHandlers(row, isSource);
                row.innerHTML = `
                    <i class="bi ${icon}"></i>
                    <div class="file-info">
                        <span class="file-name">${file.name}</span>
                        <span class="file-details">${size} ${file.modified || ''}</span>
                    </div>
                `;

                container.insertBefore(row, anchor);
            });
        }

        function addOptimisticItemsToState(isSource, targetPath, files) {
            const state = isSource ? browseState.source : browseState.target;
            if (!state || state.path !== targetPath) return false;

            const existing = new Set((state.fullItems || []).map(it => it.path));
            const items = _buildOptimisticItems(targetPath, files);
            const added = items.filter(it => it.path && !existing.has(it.path));
            if (!added.length) return false;

            state.fullItems = (state.fullItems || []).concat(added);
            state.total = (state.total || 0) + added.length;
            state.loadedCount = (state.loadedCount || 0) + added.length;

            _insertOptimisticRows(isSource, added);
            updateFileCountDisplay(isSource, state.loadedCount, state.total);
            return true;
        }

        function removeOptimisticItemsFromState(isSource, sourcePath, files) {
            const state = isSource ? browseState.source : browseState.target;
            if (!state || state.path !== sourcePath) return false;

            const removePaths = new Set((files || []).map(f => f && f.path).filter(Boolean));
            if (!removePaths.size) return false;

            const beforeCount = state.fullItems ? state.fullItems.length : 0;
            if (state.fullItems && state.fullItems.length) {
                state.fullItems = state.fullItems.filter(it => !removePaths.has(it.path));
            }
            const removedCount = Math.max(0, beforeCount - (state.fullItems ? state.fullItems.length : 0));
            if (removedCount) {
                state.total = Math.max(0, (state.total || 0) - removedCount);
                state.loadedCount = Math.max(0, (state.loadedCount || 0) - removedCount);
            }

            const nodes = getFileNodes(isSource);
            nodes.forEach(node => {
                if (removePaths.has(node.dataset.path)) {
                    node.remove();
                }
            });

            if (isSource) {
                selectedSourceFiles = (selectedSourceFiles || []).filter(f => !removePaths.has(f.path));
            } else {
                selectedTargetFiles = (selectedTargetFiles || []).filter(f => !removePaths.has(f.path));
            }

            updateFileCountDisplay(isSource, state.loadedCount, state.total);
            return removedCount > 0;
        }

        function applyTransferOptimisticUpdate() {
            if (!transferContext || !transferContext.files || transferContext.files.length === 0) {
                return { targetUpdated: false, sourceUpdated: false };
            }

            const sourcePanel = currentTransferDirection === 'rtl' ? 'target' : 'source';
            const targetPanel = currentTransferDirection === 'rtl' ? 'source' : 'target';

            let targetUpdated = false;
            let sourceUpdated = false;

            const targetPanelServer = document.getElementById(targetPanel === 'source' ? 'sourceServer' : 'targetServer').value;
            if (targetPanelServer === transferContext.targetServer) {
                targetUpdated = addOptimisticItemsToState(targetPanel === 'source', transferContext.targetPath, transferContext.files);
            }

            if ((transferContext.mode || currentTransferMode) === 'move') {
                const sourcePanelServer = document.getElementById(sourcePanel === 'source' ? 'sourceServer' : 'targetServer').value;
                if (sourcePanelServer === transferContext.sourceServer) {
                    sourceUpdated = removeOptimisticItemsFromState(sourcePanel === 'source', transferContext.sourcePath, transferContext.files);
                }
            }

            return { targetUpdated, sourceUpdated };
        }

        function applyDeleteOptimistic(type, files) {
            const isSource = type === 'source';
            const currentPath = isSource ? currentSourcePath : currentTargetPath;
            return removeOptimisticItemsFromState(isSource, currentPath, files);
        }

        function applyRenameOptimistic(type, oldPath, newName) {
            const isSource = type === 'source';
            const state = isSource ? browseState.source : browseState.target;
            const currentPath = isSource ? currentSourcePath : currentTargetPath;
            if (!state || state.path !== currentPath) return null;

            const parent = getParentPath(oldPath);
            const newPath = _joinPath(parent, newName);
            if (!newPath) return null;

            let updated = false;
            if (state.fullItems && state.fullItems.length) {
                state.fullItems = state.fullItems.map(item => {
                    if (item && item.path === oldPath) {
                        updated = true;
                        return {
                            ...item,
                            name: newName,
                            path: newPath
                        };
                    }
                    return item;
                });
            }

            const nodes = getFileNodes(isSource);
            nodes.forEach(node => {
                if (node.dataset.path === oldPath) {
                    node.dataset.path = newPath;
                    node.dataset.name = newName;
                    const nameEl = node.querySelector('.file-name');
                    if (nameEl) nameEl.textContent = newName;
                    updated = true;
                }
            });

            if (isSource) {
                selectedSourceFiles = (selectedSourceFiles || []).map(f => {
                    if (f && f.path === oldPath) {
                        return { ...f, path: newPath, name: newName };
                    }
                    return f;
                });
            } else {
                selectedTargetFiles = (selectedTargetFiles || []).map(f => {
                    if (f && f.path === oldPath) {
                        return { ...f, path: newPath, name: newName };
                    }
                    return f;
                });
            }

            if (updated) {
                updateFileCountDisplay(isSource, state.loadedCount, state.total);
            }

            return { oldPath, newPath, updated };
        }

        function _isEditableElement(el) {
            if (!el) return false;
            const tag = (el.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
            return !!el.isContentEditable;
        }

        function _hasTextSelection() {
            try {
                const sel = window.getSelection && window.getSelection();
                return !!(sel && String(sel.toString() || '').trim());
            } catch (_) {
                return false;
            }
        }

        function _getPanelServer(panel) {
            const id = panel === 'target' ? 'targetServer' : 'sourceServer';
            const el = document.getElementById(id);
            return el ? (el.value || '') : '';
        }

        function _getPanelPath(panel) {
            return panel === 'target' ? currentTargetPath : currentSourcePath;
        }

        async function startTransferWithParams(sourceServer, targetServer, targetPath, sourceFiles, mode, direction, refreshOverride = null, skipMoveConfirm = false) {
            if (isTransferring) {
                addLogWarning('⚠️ 已有传输任务在进行中');
                return false;
            }

            const files = _cloneTransferFiles(sourceFiles || []);
            if (!files.length) {
                addLogWarning('⚠️ 请选择要传输的文件或文件夹');
                return false;
            }

            const finalMode = (mode === 'move') ? 'move' : 'copy';
            currentTransferMode = finalMode;
            currentTransferDirection = direction || currentTransferDirection || 'ltr';

            const fastSSH = true;
            const parallelTransfer = true;


            if (finalMode === 'move' && !skipMoveConfirm) {
                const ok = await showConfirmDialog('当前选择的是「剪切」模式，源文件将被删除。是否继续？', {
                    title: '剪切确认',
                    danger: true
                });
                if (!ok) {
                    return false;
                }
            }


            if (!sourceServer || !targetServer || !targetPath) {
                addLogWarning('⚠️ 请选择源服务器、目标服务器和目标路径');
                await showAlertDialog('请选择源服务器、目标服务器和目标路径');
                return false;
            }


            if (sourceServer === targetServer) {
                const hasConflict = files.some(file =>
                    file.path === targetPath || targetPath.startsWith(file.path + '/')
                );
                if (hasConflict) {
                    addLogWarning('⚠️ 源路径和目标路径不能相同或存在包含关系');
                    await showAlertDialog('源路径和目标路径不能相同或存在包含关系');
                    return false;
                }
            }

            cacheTransferContext(sourceServer, targetServer, '', targetPath, files, finalMode);


            transferRefreshOverride = refreshOverride;


            isTransferring = true;


            document.getElementById('progressContainer').style.display = 'block';
            const startBtn = document.getElementById('startTransferBtn');
            if (startBtn) startBtn.style.display = 'none';
            document.getElementById('cancelTransferBtn').style.display = 'inline-block';

            const fileNames = files.map(f => f.name).join(', ');
            addLogInfo(`📤 源: ${sourceServer} (${files.length}项)`);
            addLogInfo(`📥 目标: ${targetServer}:${targetPath}`);
            addLogInfo(`📋 文件: ${fileNames.length > 50 ? fileNames.substring(0, 50) + '...' : fileNames}`);


            socket.emit('start_transfer', {
                source_server: sourceServer,
                source_files: files,
                target_server: targetServer,
                target_path: targetPath,
                mode: finalMode,
                fast_ssh: fastSSH,
                parallel_transfer: parallelTransfer
            });

            return true;
        }

        function setTransferClipboardFromSelection(mode) {
            if (isTransferring) {
                addLogWarning('⚠️ 传输进行中，暂不支持复制/剪切');
                return;
            }

            const leftSelected = selectedSourceFiles.length > 0;
            const rightSelected = selectedTargetFiles.length > 0;


            if (leftSelected && rightSelected) {
                addLogWarning('⚠️ 左右两侧同时选择了项目，请只在一侧选择');
                showAlertDialog('左右两侧同时选择了项目，请只在一侧选择');
                return;
            }
            if (!leftSelected && !rightSelected) {
                addLogWarning('⚠️ 请先选择要复制/剪切的文件或文件夹');
                return;
            }

            const sourcePanel = leftSelected ? 'source' : 'target';
            const sourceServer = _getPanelServer(sourcePanel);
            if (!sourceServer) {
                addLogWarning('⚠️ 请先选择源服务器');
                return;
            }

            const sourceFiles = _cloneTransferFiles(leftSelected ? selectedSourceFiles : selectedTargetFiles);
            transferClipboard = {
                mode: mode === 'move' ? 'move' : 'copy',
                sourcePanel,
                sourceServer,
                files: sourceFiles,
                ts: Date.now()
            };

            const opText = transferClipboard.mode === 'move' ? '剪切' : '复制';
            addLogSuccess(`📋 已${opText} ${sourceFiles.length} 项（Ctrl+V 粘贴到当前激活侧）`);
        }

        async function pasteTransferClipboard() {
            if (!transferClipboard || !transferClipboard.files || transferClipboard.files.length === 0) {
                addLogWarning('⚠️ 剪贴板为空：请先 Ctrl+C 或 Ctrl+X 选择文件');
                return;
            }
            if (isTransferring) {
                addLogWarning('⚠️ 已有传输任务在进行中');
                return;
            }


            const currentSourceServer = _getPanelServer(transferClipboard.sourcePanel);
            if (currentSourceServer && transferClipboard.sourceServer && currentSourceServer !== transferClipboard.sourceServer) {
                addLogWarning(`⚠️ 源面板服务器已从 ${transferClipboard.sourceServer} 切换为 ${currentSourceServer}，请重新 Ctrl+C/Ctrl+X 选择后再粘贴`);
                return;
            }


            const destPanel = (lastActivePanel === 'target') ? 'target' : 'source';
            const targetServer = _getPanelServer(destPanel);
            const targetPath = _getPanelPath(destPanel);

            if (!targetServer) {
                addLogWarning(destPanel === 'source' ? '⚠️ 请先选择源服务器' : '⚠️ 请先选择目标服务器');
                return;
            }
            if (!targetPath) {
                addLogWarning('⚠️ 请先浏览到目标目录');
                return;
            }

            const mode = transferClipboard.mode === 'move' ? 'move' : 'copy';
            const modeEl = document.getElementById(mode === 'move' ? 'modeMove' : 'modeCopy');
            if (modeEl) modeEl.checked = true;


            let direction = (destPanel === 'target') ? 'ltr' : 'rtl';
            if (transferClipboard.sourcePanel === 'source' && destPanel === 'target') direction = 'ltr';
            if (transferClipboard.sourcePanel === 'target' && destPanel === 'source') direction = 'rtl';

            let refreshOverride = null;
            if (destPanel === transferClipboard.sourcePanel) {
                refreshOverride = {
                    refreshSource: destPanel === 'source',
                    refreshTarget: destPanel === 'target'
                };
            }

            const started = await startTransferWithParams(
                transferClipboard.sourceServer,
                targetServer,
                targetPath,
                transferClipboard.files,
                mode,
                direction,
                refreshOverride
            );


            if (started && mode === 'move') {
                transferClipboard = null;
            }
        }

        function setupTransferClipboardHotkeys() {
            document.addEventListener('keydown', (event) => {
                const key = (event.key || '').toLowerCase();
                const isMod = event.ctrlKey || event.metaKey;
                if (!isMod) return;
                if (event.altKey || event.shiftKey) return;


                if (_isEditableElement(event.target)) return;


                if ((key === 'c' || key === 'x') && _hasTextSelection()) return;

                if (key === 'c') {
                    event.preventDefault();
                    setTransferClipboardFromSelection('copy');
                } else if (key === 'x') {
                    event.preventDefault();
                    setTransferClipboardFromSelection('move');
                } else if (key === 'v') {
                    event.preventDefault();
                    pasteTransferClipboard();
                }
            }, { capture: true });
        }

        setupTransferClipboardHotkeys();


        let downloadWindowsContext = null; // { sourcePanel, destPanel, sourceServer, files }
        const downloadWinPicker = { server: '', path: '', selectedPath: '', drives: [] };

        function _getDownloadWinAnchor() {
            const anchor = (typeof window !== 'undefined' && window.downloadWinAnchor) ? window.downloadWinAnchor : null;
            let x = anchor ? Number(anchor.x) : NaN;
            let y = anchor ? Number(anchor.y) : NaN;

            if (!Number.isFinite(x) || !Number.isFinite(y)) {
                x = window.innerWidth / 2;
                y = window.innerHeight / 4;
            }

            return { x, y };
        }

        function positionDownloadWindowsModal() {
            const modalEl = document.getElementById('downloadWindowsModal');
            const dialog = modalEl ? modalEl.querySelector('.download-win-dialog') : null;
            if (!dialog) return;

            const anchor = _getDownloadWinAnchor();
            const rect = dialog.getBoundingClientRect();
            const menuWidth = rect.width || 420;
            const menuHeight = rect.height || 520;
            const padding = 8;
            let left = anchor.x;
            let top = anchor.y;

            if (left + menuWidth > window.innerWidth - padding) {
                left = window.innerWidth - menuWidth - padding;
            }
            if (top + menuHeight > window.innerHeight - padding) {
                top = window.innerHeight - menuHeight - padding;
            }

            dialog.style.left = `${Math.max(padding, left)}px`;
            dialog.style.top = `${Math.max(padding, top)}px`;
        }

        function positionDownloadWindowsModal() {
            const modalEl = document.getElementById('downloadWindowsModal');
            const dialog = modalEl ? modalEl.querySelector('.download-win-dialog') : null;
            if (!dialog) return;

            const anchor = _getDownloadWinAnchor();
            const rect = dialog.getBoundingClientRect();
            const width = rect.width || 420;
            const height = rect.height || 520;
            const padding = 8;
            const gap = 10;

            let left = anchor.x;
            let top = anchor.y;

            if (anchor.rect) {
                left = anchor.rect.right + gap;
                top = anchor.rect.top;
            }

            const maxLeft = window.innerWidth - width - padding;
            const maxTop = window.innerHeight - height - padding;

            if (left > maxLeft) {
                const altLeft = anchor.rect ? (anchor.rect.left - width - gap) : maxLeft;
                left = (Number.isFinite(altLeft) && altLeft >= padding) ? altLeft : maxLeft;
            }

            if (top > maxTop) {
                const altTop = anchor.rect ? (anchor.rect.bottom - height) : maxTop;
                top = Math.max(padding, altTop);
            }

            if (left < padding) left = padding;
            if (top < padding) top = padding;

            dialog.style.left = `${left}px`;
            dialog.style.top = `${top}px`;
        }

        function positionDownloadWindowsModal() {
            const modalEl = document.getElementById('downloadWindowsModal');
            const dialog = modalEl ? modalEl.querySelector('.download-win-dialog') : null;
            if (!dialog) return;

            const anchor = _getDownloadWinAnchor();
            const rect = dialog.getBoundingClientRect();
            const width = rect.width || 420;
            const height = rect.height || 520;
            const padding = 8;

            let left = anchor.x;
            let top = anchor.y;

            if (left + width > window.innerWidth - padding) {
                left = window.innerWidth - width - padding;
            }
            if (top + height > window.innerHeight - padding) {
                top = window.innerHeight - height - padding;
            }

            if (left < padding) left = padding;
            if (top < padding) top = padding;

            dialog.style.left = `${left}px`;
            dialog.style.top = `${top}px`;
        }


        function _listWindowsServers() {
            try {
                return Object.entries(SERVERS_DATA || {})
                    .filter(([ip, meta]) => meta && meta.os_type === 'windows')
                    .map(([ip, meta]) => ({ ip, meta }));
            } catch (_) {
                return [];
            }
        }

        function _suggestWindowsServer(windowsServers) {
            const list = Array.isArray(windowsServers) ? windowsServers : _listWindowsServers();
            if (!list.length) return '';

            const clientIp = (typeof window !== 'undefined' && window.CLIENT_IPV4) ? window.CLIENT_IPV4 : null;
            if (clientIp) {
                const hit = list.find(s => s.ip === clientIp);
                if (hit) return hit.ip;
            }

            const targetServer = (document.getElementById('targetServer') || {}).value || '';
            if (targetServer && isWindowsServer(targetServer)) return targetServer;

            const sourceServer = (document.getElementById('sourceServer') || {}).value || '';
            if (sourceServer && isWindowsServer(sourceServer)) return sourceServer;

            return list[0].ip;
        }

        function _normalizeSlashes(p) {
            return String(p || '').replace(/\\/g, '/');
        }

        function _normalizeDriveLetter(letter) {
            if (!letter) return '';
            const up = String(letter).trim().toUpperCase();
            return up.endsWith(':') ? up : `${up}:`;
        }

        function _driveRoot(letter) {
            const d = _normalizeDriveLetter(letter);
            return d ? `${d}/` : '';
        }

        function _normalizeWinPath(p) {
            let path0 = _normalizeSlashes(p).trim();
            if (!path0) return '';

            const m = path0.match(/^([a-zA-Z]:)(?:\/(.*))?$/);
            if (m) {
                const drive = String(m[1]).toUpperCase();
                const rest = m[2] ? String(m[2]) : '';
                path0 = rest ? `${drive}/${rest}` : `${drive}/`;
            }

            if (/^[A-Z]:$/.test(path0)) {
                path0 = `${path0}/`;
            }

            if (path0.length > 3 && path0.endsWith('/')) {
                path0 = path0.slice(0, -1);
            }

            return path0;
        }

        function _getWinParent(p) {
            const path0 = _normalizeWinPath(p);
            if (!path0) return '';
            if (/^[A-Z]:\/$/.test(path0)) return path0;
            const idx = path0.lastIndexOf('/');
            if (idx <= 2) return path0.slice(0, 3);
            return path0.slice(0, idx);
        }

        function _renderWindowsServerOptions(selectEl, list) {
            if (!selectEl) return;
            selectEl.innerHTML = '';
            list.forEach(({ ip, meta }) => {
                const opt = document.createElement('option');
                opt.value = ip;
                const name = (meta && meta.name) ? meta.name : ip;
                opt.textContent = `${name} (${ip})`;
                selectEl.appendChild(opt);
            });
        }

        async function downloadWinPickerLoadDrives(serverIP) {
            const driveSelect = document.getElementById('downloadWindowsDriveSelect');
            if (!driveSelect) return;

            driveSelect.innerHTML = '';
            driveSelect.disabled = true;
            downloadWinPicker.drives = [];

            if (!serverIP) {
                driveSelect.innerHTML = '<option value="">(未选择)</option>';
                return;
            }

            try {
                const resp = await fetch(`/api/windows_drives/${serverIP}`, { cache: 'no-cache' });
                const data = await resp.json();

                if (data.success && Array.isArray(data.drives) && data.drives.length) {
                    downloadWinPicker.drives = data.drives;
                    data.drives.forEach(d => {
                        const root = _driveRoot(d.letter);
                        if (!root) return;
                        const opt = document.createElement('option');
                        opt.value = root;
                        opt.textContent = root;
                        driveSelect.appendChild(opt);
                    });
                    driveSelect.disabled = false;

                    const cur = _normalizeWinPath(downloadWinPicker.path || '');
                    const curDrive = _normalizeDriveLetter((cur.split('/')[0] || ''));
                    const prefer = data.drives.find(dd => _normalizeDriveLetter(dd.letter) === curDrive)
                        || data.drives.find(dd => _normalizeDriveLetter(dd.letter) === 'C:')
                        || data.drives[0];
                    if (prefer) {
                        driveSelect.value = _driveRoot(prefer.letter);
                    }
                } else {
                    driveSelect.innerHTML = '<option value="">(无磁盘信息)</option>';
                }
            } catch (_) {
                driveSelect.innerHTML = '<option value="">(加载失败)</option>';
            }
        }

        function _updateDownloadWinDirActive() {
            const listEl = document.getElementById('downloadWindowsDirList');
            if (!listEl) return;
            const selected = downloadWinPicker.selectedPath || '';
            Array.from(listEl.querySelectorAll('.list-group-item')).forEach(el => {
                if (selected && el.dataset.path === selected) {
                    el.classList.add('active');
                } else {
                    el.classList.remove('active');
                }
            });
        }

        function downloadWinPickerSelectPath(p) {
            const path0 = _normalizeWinPath(p);
            if (!path0) return;
            downloadWinPicker.selectedPath = path0;
            const input = document.getElementById('downloadWindowsPathInput');
            if (input) input.value = path0;
            _updateDownloadWinDirActive();
        }

        async function downloadWinPickerLoadDirs() {
            const listEl = document.getElementById('downloadWindowsDirList');
            if (!listEl) return;

            const serverIP = downloadWinPicker.server;
            const p = downloadWinPicker.path;
            if (!serverIP) {
                listEl.innerHTML = '<div class="list-group-item text-muted">未找到Windows服务器</div>';
                return;
            }
            if (!p) {
                listEl.innerHTML = '<div class="list-group-item text-muted">请选择磁盘</div>';
                return;
            }

            listEl.innerHTML = '<div class="list-group-item text-muted">加载中...</div>';

            try {
                const params = new URLSearchParams({
                    path: p,
                    show_hidden: 'false',
                    offset: '0',
                    limit: '2000'
                });
                const resp = await fetch(`/api/browse/${serverIP}?${params.toString()}`, { cache: 'no-cache' });
                const data = await resp.json();
                if (!data.success) {
                    listEl.innerHTML = `<div class="list-group-item text-danger">浏览失败: ${data.error || '未知错误'}</div>`;
                    return;
                }

                const dirs = (data.files || []).filter(f => f && f.is_directory);
                dirs.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN', { numeric: true, sensitivity: 'base' }));

                listEl.innerHTML = '';

                const current = _normalizeWinPath(p);
                const parent = _getWinParent(current);
                if (parent && parent !== current) {
                    const upBtn = document.createElement('button');
                    upBtn.type = 'button';
                    upBtn.className = 'list-group-item list-group-item-action d-flex align-items-center';
                    upBtn.dataset.path = parent;

                    const icon = document.createElement('i');
                    icon.className = 'bi bi-arrow-90deg-up text-secondary me-2';
                    const name = document.createElement('span');
                    name.textContent = '上一级';

                    upBtn.appendChild(icon);
                    upBtn.appendChild(name);

                    upBtn.addEventListener('click', () => downloadWinPickerEnterPath(upBtn.dataset.path));
                    upBtn.addEventListener('dblclick', () => downloadWinPickerEnterPath(upBtn.dataset.path));

                    listEl.appendChild(upBtn);
                }

                if (!dirs.length) {
                    const empty = document.createElement('div');
                    empty.className = 'list-group-item text-muted';
                    empty.textContent = '（此目录下没有文件夹）';
                    listEl.appendChild(empty);
                    return;
                }

                dirs.forEach(d => {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'list-group-item list-group-item-action d-flex align-items-center';
                    btn.dataset.path = d.path || '';

                    const icon = document.createElement('i');
                    icon.className = 'bi bi-folder-fill text-warning me-2';
                    const name = document.createElement('span');
                    name.textContent = d.name || d.path || '';

                    btn.appendChild(icon);
                    btn.appendChild(name);

                    btn.addEventListener('click', () => downloadWinPickerSelectPath(btn.dataset.path));
                    btn.addEventListener('dblclick', () => downloadWinPickerEnterPath(btn.dataset.path));

                    listEl.appendChild(btn);
                });

                _updateDownloadWinDirActive();
                positionDownloadWindowsModal();
            } catch (e) {
                listEl.innerHTML = `<div class="list-group-item text-danger">浏览失败: ${String(e)}</div>`;
            }
        }

        async function downloadWinPickerEnterPath(p) {
            const serverIP = downloadWinPicker.server;
            if (!serverIP) return;

            const path0 = _normalizeWinPath(p);
            if (!path0) return;

            downloadWinPicker.path = path0;
            downloadWinPicker.selectedPath = path0;

            const input = document.getElementById('downloadWindowsPathInput');
            if (input) input.value = path0;

            const driveSelect = document.getElementById('downloadWindowsDriveSelect');
            if (driveSelect && !driveSelect.disabled) {
                const drive = _normalizeDriveLetter((path0.split('/')[0] || ''));
                const want = drive ? `${drive}/` : '';
                if (want) driveSelect.value = want;
            }

            await downloadWinPickerLoadDirs();
        }

        function downloadWinPickerRefresh() {
            downloadWinPickerLoadDirs();
        }

        function downloadWinPickerGoUp() {
            const parent = _getWinParent(downloadWinPicker.path || '');
            if (parent) downloadWinPickerEnterPath(parent);
        }

        function downloadWinPickerNavigateToInput() {
            const input = document.getElementById('downloadWindowsPathInput');
            const p = input ? (input.value || '').trim() : '';
            if (!p) return;
            downloadWinPickerEnterPath(p);
        }

        async function downloadWinPickerSetServer(serverIP) {
            if (!serverIP) return;

            downloadWinPicker.server = serverIP;

            const isDestSource = downloadWindowsContext ? (downloadWindowsContext.destPanel === 'source') : false;
            const remembered = getDefaultPathWithRemember(serverIP, isDestSource);
            const fallback = (SERVERS_DATA && SERVERS_DATA[serverIP] && SERVERS_DATA[serverIP].default_path) ? SERVERS_DATA[serverIP].default_path : 'C:/';

            const p = _normalizeWinPath(remembered || fallback);
            downloadWinPicker.path = p;
            downloadWinPicker.selectedPath = p;

            const input = document.getElementById('downloadWindowsPathInput');
            if (input) input.value = p;

            await Promise.all([
                downloadWinPickerLoadDrives(serverIP),
                downloadWinPickerLoadDirs()
            ]);
        }

        async function openDownloadToWindowsModal() {
            if (isTransferring) {
                addLogWarning('⚠️ 已有传输任务在进行中');
                return;
            }

            const leftSelected = selectedSourceFiles.length > 0;
            const rightSelected = selectedTargetFiles.length > 0;

            if (leftSelected && rightSelected) {
                addLogWarning('⚠️ 左右两侧同时选择了项目，请只在一侧选择');
                await showAlertDialog('左右两侧同时选择了项目，请只在一侧选择');
                return;
            }
            if (!leftSelected && !rightSelected) {
                addLogWarning('⚠️ 请先选择要下载的文件或文件夹');
                return;
            }

            const sourcePanel = leftSelected ? 'source' : 'target';
            const destPanel = leftSelected ? 'target' : 'source';
            const sourceServer = _getPanelServer(sourcePanel);
            if (!sourceServer) {
                addLogWarning('⚠️ 请先选择源服务器');
                return;
            }

            const files = _cloneTransferFiles(leftSelected ? selectedSourceFiles : selectedTargetFiles);
            if (!files.length) {
                addLogWarning('⚠️ 请选择要下载的文件或文件夹');
                return;
            }

            const windowsServers = _listWindowsServers();
            if (!windowsServers.length) {
                addLogWarning('⚠️ 未配置Windows服务器，无法下载到Windows');
                await showAlertDialog('未配置Windows服务器，无法下载到Windows');
                return;
            }

            downloadWindowsContext = { sourcePanel, destPanel, sourceServer, files };

            const suggested = _suggestWindowsServer(windowsServers);
            if (!suggested) {
                addLogWarning('⚠️ 未找到Windows服务器');
                await showAlertDialog('未找到Windows服务器');
                return;
            }

            const listEl = document.getElementById('downloadWindowsDirList');
            if (listEl) listEl.innerHTML = '<div class="list-group-item text-muted">加载中...</div>';

            const driveSelect = document.getElementById('downloadWindowsDriveSelect');
            if (driveSelect) {
                driveSelect.innerHTML = '<option value="">加载中...</option>';
                driveSelect.disabled = true;
                driveSelect.onchange = () => {
                    const v = driveSelect.value || '';
                    if (v) downloadWinPickerEnterPath(v);
                };
            }

            const modalEl = document.getElementById('downloadWindowsModal');
            if (modalEl && typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                const inst = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl, { backdrop: true, keyboard: true });
                inst.show();
                requestAnimationFrame(() => positionDownloadWindowsModal());
            }

            downloadWinPickerSetServer(suggested)
                .then(() => positionDownloadWindowsModal())
                .catch(() => {});
        }

        async function confirmDownloadToWindows() {
            if (!downloadWindowsContext) {
                addLogWarning('⚠️ 下载上下文丢失，请重新右键选择');
                return;
            }

            const winServer = downloadWinPicker.server || '';
            if (!winServer) {
                addLogWarning('⚠️ 未找到Windows服务器');
                return;
            }

            const targetPathRaw = (downloadWinPicker.selectedPath || downloadWinPicker.path || '').trim();
            const targetPath = _normalizeWinPath(targetPathRaw);
            if (!targetPath) {
                addLogWarning('⚠️ 请选择目标目录');
                return;
            }


            const destPanel = downloadWindowsContext.destPanel;
            const isDestSource = destPanel === 'source';
            updateRememberedCache(destPanel, winServer, targetPath);

            const destSelect = document.getElementById(isDestSource ? 'sourceServer' : 'targetServer');
            if (destSelect) {
                destSelect.value = winServer;
                destSelect.dispatchEvent(new Event('change'));
            }

            const modeEl = document.getElementById('modeCopy');
            if (modeEl) modeEl.checked = true;

            const direction = downloadWindowsContext.sourcePanel === 'source' ? 'ltr' : 'rtl';
            const started = await startTransferWithParams(
                downloadWindowsContext.sourceServer,
                winServer,
                targetPath,
                downloadWindowsContext.files,
                'copy',
                direction,
                null
            );

            if (started) {
                const modalEl = document.getElementById('downloadWindowsModal');
                if (modalEl && typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                    const inst = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
                    inst.hide();
                }
            }
        }


        const TURBOFILE_BOOT = (typeof window !== 'undefined' && window.TURBOFILE_BOOT) ? window.TURBOFILE_BOOT : {};
        const SERVERS_DATA = TURBOFILE_BOOT.servers || {};
        const REMEMBERED_PATHS = TURBOFILE_BOOT.remembered_paths || {};


        let logBuffer = [];
        let logUpdateTimer = null;
        let isTransferring = false;
        const MAX_LOG_ENTRIES = 100;
        const LOG_UPDATE_INTERVAL = 500;
        const runLogBlocks = {};
        let currentRunId = null;
        let socketId = null;
        let imageZoom = 1;
        let imageOffsetX = 0;
        let imageOffsetY = 0;


        function addLog(message, type = 'info') {

            if (isTransferring && isProgressMessage(message)) {
                return;
            }

            const timestamp = new Date().toLocaleTimeString();
            const logEntry = {
                timestamp: timestamp,
                message: message,
                type: type,
                id: Date.now() + Math.random()
            };


            logBuffer.push(logEntry);


            console.log(`[${timestamp}] ${message}`);


            scheduleLogUpdate();
        }


        function isProgressMessage(message) {
            const progressKeywords = [
                '字节', 'bytes', '%', 'MB/s', 'KB/s', 'GB/s',
                '进度', 'progress', '传输速度', '剩余时间'
            ];
            return progressKeywords.some(keyword => message.includes(keyword));
        }


        function scheduleLogUpdate() {
            if (logUpdateTimer) {
                clearTimeout(logUpdateTimer);
            }

            logUpdateTimer = setTimeout(() => {
                updateLogDisplay();
                logUpdateTimer = null;
            }, LOG_UPDATE_INTERVAL);
        }


        function flushLogNow() {
            if (logUpdateTimer) {
                clearTimeout(logUpdateTimer);
                logUpdateTimer = null;
            }
            updateLogDisplay();
        }


        function updateLogDisplay() {
            if (logBuffer.length === 0) return;

            const logContent = document.getElementById('logContent');
            const logContainer = document.getElementById('logContainer');


            const fragment = document.createDocumentFragment();

            logBuffer.forEach(entry => {
                const logDiv = document.createElement('div');
                logDiv.className = `log-entry log-${entry.type}`;
                logDiv.innerHTML = `
                    <span class="log-timestamp">${entry.timestamp}</span>
                    <span class="log-message">${escapeHtml(entry.message)}</span>
                `;
                fragment.appendChild(logDiv);
            });

            logContent.appendChild(fragment);


            const entries = logContent.querySelectorAll('.log-entry');
            if (entries.length > MAX_LOG_ENTRIES) {
                const removeCount = entries.length - MAX_LOG_ENTRIES;
                for (let i = 0; i < removeCount; i++) {
                    entries[i].remove();
                }
            }


            logContainer.scrollTop = logContainer.scrollHeight;


            logBuffer = [];
        }


        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }


        function showLogModal() {
            const logModal = new bootstrap.Modal(document.getElementById('logModal'));
            logModal.show();


            setTimeout(() => {
                const logContainer = document.getElementById('logContainer');
                if (logContainer) {
                    logContainer.scrollTop = logContainer.scrollHeight;
                }
            }, 300);
        }


        function clearLog() {
            document.getElementById('logContent').innerHTML = '';
            logBuffer = [];
            addLog('📝 日志已清空', 'info');
        }


        function addLogInfo(message) {
            addLog(message, 'info');
        }

        function addLogSuccess(message) {
            addLog(message, 'success');
        }

        const toastConfig = {
            duration: 2400,
            maxVisible: 3
        };

        function showToast(message, type = 'info') {
            const container = document.getElementById('toastContainer');
            if (!container || !message) return;
            const item = document.createElement('div');
            item.className = `toast-item toast-${type}`;
            const msg = document.createElement('div');
            msg.className = 'toast-message';
            msg.textContent = message;
            item.appendChild(msg);
            container.appendChild(item);
            while (container.children.length > toastConfig.maxVisible) {
                container.removeChild(container.firstChild);
            }
            setTimeout(() => {
                if (item.parentNode) item.parentNode.removeChild(item);
            }, toastConfig.duration);
        }


        function copyTextToClipboard(text) {
            if (navigator.clipboard && window.isSecureContext) {
                return navigator.clipboard.writeText(text);
            } else {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                try {
                    document.execCommand('copy');
                    return Promise.resolve();
                } catch (e) {
                    return Promise.reject(e);
                } finally {
                    document.body.removeChild(textarea);
                }
            }
        }


        function copyPath(type) {
            const text = type === 'source' ? (currentSourcePath || '') : (currentTargetPath || '');
            if (!text) {
                addLogWarning('⚠️ 当前路径为空，无法复制');
                return;
            }
            copyTextToClipboard(text)
                .then(() => addLogSuccess(`📋 已复制路径: ${text}`))
                .catch(() => addLogWarning('⚠️ 复制失败，请手动复制'));
        }


        async function deleteSelected(type, event) {
            const blurBtn = () => {
                if (event && event.currentTarget && typeof event.currentTarget.blur === 'function') {
                    event.currentTarget.blur();
                }
            };
            blurBtn();
            const selectedFiles = type === 'source' ? selectedSourceFiles : selectedTargetFiles;
            const server = type === 'source' ? document.getElementById('sourceServer').value : document.getElementById('targetServer').value;

            if (!server) {
                addLogWarning('⚠️ 请先选择服务器');
                return;
            }

            if (selectedFiles.length === 0) {
                addLogWarning('⚠️ 请先选择要删除的文件或文件夹');
                return;
            }


            const ok = await showConfirmDialog(`确定要删除以下 ${selectedFiles.length} 项吗？`, {
                title: '删除确认',
                items: selectedFiles.map(f => f.name),
                warning: '此操作不可恢复！',
                danger: true,
                confirmText: '删除'
            });
            if (!ok) {
                blurBtn();
                return;
            }
            blurBtn();

            try {

                const paths = selectedFiles.map(f => f.path);

                const response = await fetch('/api/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        server: server,
                        paths: paths
                    })
                });

                const result = await response.json();

                const silentRefresh = () => {
                    if (type === 'source') {
                        refreshSourceAsync({ silent: true });
                    } else {
                        refreshTargetAsync({ silent: true });
                    }
                };

                if (result.success || (result.deleted_count && result.deleted_count > 0)) {
                    if (type === 'source') {
                        selectedSourceFiles = [];
                    } else {
                        selectedTargetFiles = [];
                    }
                    if (result.failed_items && result.failed_items.length > 0) {
                        showToast(`⚠️ 部分删除失败 (${result.failed_items.length})`, 'warning');
                    } else {
                        const countText = result.deleted_count ? `${result.deleted_count} 项` : '已删除';
                        showToast(`🗑️ 删除完成: ${countText}`, 'success');
                    }
                    if (!result.success && result.failed_items && result.failed_items.length > 0) {
                        addLogWarning(`⚠️ 部分删除失败，共 ${result.failed_items.length} 项`);
                        result.failed_items.forEach(item => {
                            addLogError(`  - ${item.path}: ${item.error}`);
                        });
                    }
                } else {
                    addLogError(`❌ 删除失败: ${result.error || '未知错误'}`);
                    showToast('❌ 删除失败', 'error');
                    if (result.failed_items && result.failed_items.length > 0) {
                        result.failed_items.forEach(item => {
                            addLogError(`  - ${item.path}: ${item.error}`);
                        });
                    }
                }
                silentRefresh();
            } catch (error) {
                addLogError(`❌ 删除操作异常: ${error.message}`);
                showToast('❌ 删除异常', 'error');
                if (type === 'source') {
                    refreshSourceAsync({ silent: true });
                } else {
                    refreshTargetAsync({ silent: true });
                }
            }
        }

        async function deletePathsDirect(server, paths, isSource) {
            const list = (paths || []).filter(Boolean);
            if (!server) {
                addLogWarning('⚠️ 请先选择服务器');
                return false;
            }
            if (!list.length) {
                addLogWarning('⚠️ 没有可删除的项目');
                return false;
            }
            if (imageDeleteInFlight) return false;
            imageDeleteInFlight = true;

            try {
                const response = await fetch('/api/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        server: server,
                        paths: list
                    })
                });

                const result = await response.json();
                const ok = result && (result.success || (result.deleted_count && result.deleted_count > 0));

                if (ok) {
                    applyDeleteOptimistic(isSource ? 'source' : 'target', list.map(p => ({ path: p })));
                    invalidatePreviewCache(server, list);
                    if (result.failed_items && result.failed_items.length > 0) {
                        showToast(`⚠️ 部分删除失败 (${result.failed_items.length})`, 'warning');
                        if (!result.success) {
                            addLogWarning(`⚠️ 部分删除失败，共 ${result.failed_items.length} 项`);
                            result.failed_items.forEach(item => {
                                addLogError(`  - ${item.path}: ${item.error}`);
                            });
                        }
                    } else {
                        const countText = result.deleted_count ? `${result.deleted_count} 项` : `${list.length} 项`;
                        showToast(`🗑️ 删除完成: ${countText}`, 'success');
                    }
                } else {
                    addLogError(`❌ 删除失败: ${result.error || '未知错误'}`);
                    showToast('❌ 删除失败', 'error');
                    if (result.failed_items && result.failed_items.length > 0) {
                        result.failed_items.forEach(item => {
                            addLogError(`  - ${item.path}: ${item.error}`);
                        });
                    }
                }
                return ok;
            } catch (error) {
                addLogError(`❌ 删除操作异常: ${error.message}`);
                showToast('❌ 删除异常', 'error');
                return false;
            } finally {
                imageDeleteInFlight = false;
                if (isSource) {
                    refreshSourceAsync({ silent: true });
                } else {
                    refreshTargetAsync({ silent: true });
                }
            }
        }


        async function showCreateFolderDialog(type) {
            const server = type === 'source' ? document.getElementById('sourceServer').value : document.getElementById('targetServer').value;
            const currentPath = type === 'source' ? currentSourcePath : currentTargetPath;

            if (!server) {
                addLogWarning('⚠️ 请先选择服务器');
                return;
            }

            if (!currentPath) {
                addLogWarning('⚠️ 请先浏览到目标目录');
                return;
            }

            const folderName = await showPromptDialog('请输入新文件夹名称', {
                title: '新建文件夹',
                defaultValue: '新建文件夹',
                placeholder: '新建文件夹'
            });
            if (!folderName || folderName.trim() === '') {
                return;
            }

            createFolder(type, server, currentPath, folderName.trim());
        }


        async function createFolder(type, server, parentPath, folderName) {
            try {
                addLogInfo(`📁 正在创建文件夹: ${folderName}...`);
                const optimisticItem = { name: folderName, path: _joinPath(parentPath, folderName), is_directory: true };
                const optimisticAdded = addOptimisticItemsToState(type === 'source', parentPath, [optimisticItem]);

                const response = await fetch('/api/create_folder', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        server: server,
                        parent_path: parentPath,
                        folder_name: folderName
                    })
                });

                const result = await response.json();

                if (result.success) {
                    addLogSuccess(`✅ ${result.message}`);

                    if (type === 'source') {
                        refreshSourceAsync({ silent: true });
                    } else {
                        refreshTargetAsync({ silent: true });
                    }
                } else {
                    addLogError(`❌ 创建文件夹失败: ${result.error}`);
                    if (optimisticAdded) {
                        removeOptimisticItemsFromState(type === 'source', parentPath, [optimisticItem]);
                    }
                }
            } catch (error) {
                addLogError(`❌ 创建文件夹异常: ${error.message}`);
                if (type === 'source') {
                    refreshSourceAsync({ silent: true });
                } else {
                    refreshTargetAsync({ silent: true });
                }
            }
        }


        async function createFile(type, server, parentPath, fileName) {
            try {
                addLogInfo(`📄 正在创建文件: ${fileName}...`);
                const optimisticItem = { name: fileName, path: _joinPath(parentPath, fileName), is_directory: false };
                const optimisticAdded = addOptimisticItemsToState(type === 'source', parentPath, [optimisticItem]);
                const response = await fetch('/api/create_file', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        server: server,
                        parent_path: parentPath,
                        file_name: fileName
                    })
                });
                const result = await response.json();
                if (result.success) {
                    addLogSuccess(`✅ ${result.message || '创建文件成功'}`);
                    if (type === 'source') {
                        refreshSourceAsync({ silent: true });
                    } else {
                        refreshTargetAsync({ silent: true });
                    }
                } else {
                    addLogError(`❌ 创建文件失败: ${result.error || '未知错误'}`);
                    if (optimisticAdded) {
                        removeOptimisticItemsFromState(type === 'source', parentPath, [optimisticItem]);
                    }
                }
            } catch (error) {
                addLogError(`❌ 创建文件异常: ${error.message}`);
                if (type === 'source') {
                    refreshSourceAsync({ silent: true });
                } else {
                    refreshTargetAsync({ silent: true });
                }
            }
        }


        async function showRenameDialog(type) {
            const selectedFiles = type === 'source' ? selectedSourceFiles : selectedTargetFiles;
            const server = type === 'source' ? document.getElementById('sourceServer').value : document.getElementById('targetServer').value;

            if (!server) {
                addLogWarning('⚠️ 请先选择服务器');
                return;
            }

            if (selectedFiles.length === 0) {
                addLogWarning('⚠️ 请先选择要重命名的文件或文件夹');
                return;
            }

            if (selectedFiles.length > 1) {
                addLogWarning('⚠️ 一次只能重命名一个文件或文件夹');
                return;
            }

            const file = selectedFiles[0];
            const oldName = file.name;
            const newName = await showPromptDialog(`请输入新名称\n原名称: ${oldName}`, {
                title: '重命名',
                defaultValue: oldName,
                placeholder: oldName
            });

            if (!newName || newName.trim() === '') {
                return;
            }

            if (newName === oldName) {
                addLogWarning('⚠️ 新名称与原名称相同');
                return;
            }

            renameFile(type, server, file.path, newName.trim());
        }


        async function renameFile(type, server, oldPath, newName) {
            try {
                addLogInfo(`✏️ 正在重命名为: ${newName}...`);
                const oldName = _getBaseName(oldPath);
                const optimistic = applyRenameOptimistic(type, oldPath, newName);

                const response = await fetch('/api/rename', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        server: server,
                        old_path: oldPath,
                        new_name: newName
                    })
                });

                const result = await response.json();

                if (result.success) {
                    addLogSuccess(`✅ ${result.message}`);


                    if (type === 'source') {
                        selectedSourceFiles = [];
                    } else {
                        selectedTargetFiles = [];
                    }

                    if (type === 'source') {
                        refreshSourceAsync({ silent: true });
                    } else {
                        refreshTargetAsync({ silent: true });
                    }
                } else {
                    addLogError(`❌ 重命名失败: ${result.error}`);
                    if (optimistic && oldName) {
                        applyRenameOptimistic(type, optimistic.newPath, oldName);
                    } else if (type === 'source') {
                        refreshSourceAsync({ silent: true });
                    } else {
                        refreshTargetAsync({ silent: true });
                    }
                }
            } catch (error) {
                addLogError(`❌ 重命名异常: ${error.message}`);
                if (type === 'source') {
                    refreshSourceAsync({ silent: true });
                } else {
                    refreshTargetAsync({ silent: true });
                }
            }
        }


        function isRunnableFileName(name) {
            if (!name) return false;
            const lower = name.toLowerCase();
            return lower.endsWith('.py') || lower.endsWith('.sh');
        }

        function addLogWarning(message) {
            addLog(message, 'warning');
        }

        function addLogError(message) {
            addLog(message, 'error');
        }

        function updateRunControls() {
            const stopBtn = document.getElementById('runStopBtn');
            const sendBtn = document.getElementById('runSendBtn');
            const input = document.getElementById('runInputBox');
            const disabled = !currentRunId;
            if (stopBtn) stopBtn.disabled = disabled;
            if (sendBtn) sendBtn.disabled = disabled;
            if (input) input.disabled = disabled;
        }

        function logCommandOutput(text, isError = false) {
            if (!text) return;
            const lines = text.split(/\r?\n/).filter(line => line.trim() !== '');
            lines.forEach(line => addLog(line, isError ? 'error' : 'info'));
        }

        async function cancelCurrentRun() {
            if (!currentRunId) {
                addLogWarning('当前没有正在运行的脚本');
                return;
            }
            try {
                const resp = await fetch('/api/run_file/cancel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ run_id: currentRunId })
                });
                const result = await resp.json();
                if (result.success) {
                    addLogWarning('⏹️ 已发送中断请求');
                } else {
                    addLogError(`❌ 中断失败: ${result.error || '未知错误'}`);
                }
            } catch (err) {
                addLogError(`❌ 中断异常: ${err.message}`);
            }
        }

        async function sendRunInput() {
            const inputEl = document.getElementById('runInputBox');
            if (!inputEl) return;
            const val = inputEl.value;
            if (!val || !val.trim()) return;
            if (!currentRunId) {
                addLogWarning('当前没有正在运行的脚本');
                return;
            }
            try {
                const resp = await fetch('/api/run_file/input', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ run_id: currentRunId, data: val })
                });
                const result = await resp.json();
                if (!result.success) {
                    addLogError(`❌ 发送输入失败: ${result.error || '未知错误'}`);
                }
            } catch (err) {
                addLogError(`❌ 发送输入异常: ${err.message}`);
            } finally {
                inputEl.value = '';
            }
        }

        function appendRunLog(runId, text, isError = false, isFinal = false, exitCode = null) {
            if (!text && !isFinal) return;
            const logContent = document.getElementById('logContent');
            const logContainer = document.getElementById('logContainer');
            if (!logContent) return;

            let block = runLogBlocks[runId];
            if (!block) {
                block = document.createElement('div');
                block.className = `log-entry ${isError ? 'log-error' : 'log-info'}`;
                const time = new Date().toLocaleTimeString();
                block.innerHTML = `
                    <span class="log-timestamp">${time}</span>
                    <pre class="log-run-block"></pre>
                `;
                logContent.appendChild(block);
                runLogBlocks[runId] = block;

                block._currentLine = '';
            }

            const pre = block.querySelector('.log-run-block');
            if (pre && text) {


                if (block._completedLines === undefined) {
                    block._completedLines = '';
                }
                let completedLines = block._completedLines;
                let currentLine = block._currentLine || '';

                for (let i = 0; i < text.length; i++) {
                    const char = text[i];
                    if (char === '\r') {


                        if (text[i + 1] === '\n') {
                            continue;
                        }

                        currentLine = '';
                    } else if (char === '\n') {

                        completedLines += currentLine + '\n';
                        currentLine = '';
                    } else {

                        currentLine += char;
                    }
                }


                block._completedLines = completedLines;
                block._currentLine = currentLine;

                pre.textContent = completedLines + currentLine;
            }

            if (isFinal && pre) {
                if (exitCode !== null && text.indexOf('退出码') === -1) {
                    pre.textContent += `\n[exit ${exitCode}]`;
                }
            }

            block.className = `log-entry ${isError ? 'log-error' : 'log-info'}`;

            if (logContainer) {
                logContainer.scrollTop = logContainer.scrollHeight;
            }

            if (isFinal) {
                delete runLogBlocks[runId];
            }
        }


        async function runFileOnServer(server, filePath, fileName) {
            if (!server) {
                addLogWarning('⚠️ 请先选择服务器');
                return;
            }
            if (!filePath) {
                addLogWarning('⚠️ 未找到要运行的文件');
                return;
            }

            addLogInfo(`▶️ 正在运行: ${fileName || filePath}`);

            if (typeof flushLogNow === 'function') {
                flushLogNow();
            }

            try {
                const resp = await fetch('/api/run_file', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ server: server, path: filePath, sid: socket.id })
                });

                const result = await resp.json();

                if (result.success) {
                    const runId = result.run_id || '';
                    currentRunId = runId || null;
                    updateRunControls();
                } else {
                    addLogError(`❌ 运行失败: ${result.error || '未知错误'}`);
                }
            } catch (error) {
                addLogError(`❌ 运行异常: ${error.message}`);
            }
        }


        async function computeSizeOnServer(server, filePath, fileName) {
            if (!server) {
                addLogWarning('⚠️ 请先选择服务器');
                return;
            }
            if (!filePath) {
                addLogWarning('⚠️ 未找到要计算的路径');
                return;
            }
            addLogInfo(`📦 正在计算大小: ${fileName || filePath}`);
            if (typeof flushLogNow === 'function') flushLogNow();
            try {
                const resp = await fetch('/api/compute_size', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ server: server, path: filePath })
                });
                const result = await resp.json();
                if (result.success) {
                    const sizeText = result.human_size || result.size_bytes + ' bytes';
                    addLogSuccess(`📏 大小: ${sizeText} (${fileName || filePath})`);
                    showToast(`📏 大小: ${sizeText}`, 'success');
                } else {
                    addLogError(`❌ 计算失败: ${result.error || '未知错误'}`);
                    showToast('❌ 计算失败', 'error');
                }
            } catch (err) {
                addLogError(`❌ 计算异常: ${err.message}`);
                showToast('❌ 计算异常', 'error');
            }
        }


        async function compressPathOnServer(server, filePath, fileName) {
            try {
                const resp = await fetch('/api/compress', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ server: server, path: filePath })
                });
                const result = await resp.json();
                if (result.success) {
                    const zipName = result.zip_name || fileName + '.zip';
                    addLogSuccess(`🗜️ 压缩完成: ${zipName}`);
                    showToast(`🗜️ 压缩完成: ${zipName}`, 'success');
                } else {
                    addLogError(`❌ 压缩失败: ${result.error || '未知错误'}`);
                    showToast('❌ 压缩失败', 'error');
                }
            } catch (err) {
                addLogError(`❌ 压缩异常: ${err.message}`);
                showToast('❌ 压缩异常', 'error');
            }
        }


        async function extractArchiveOnServer(server, filePath, fileName) {
            try {
                const resp = await fetch('/api/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ server: server, path: filePath })
                });
                const result = await resp.json();
                if (result.success) {
                    addLogSuccess(`📂 解压完成: ${fileName}`);
                    showToast(`📂 解压完成: ${fileName}`, 'success');
                } else {
                    addLogError(`❌ 解压失败: ${result.error || '未知错误'}`);
                    showToast('❌ 解压失败', 'error');
                }
            } catch (err) {
                addLogError(`❌ 解压异常: ${err.message}`);
                showToast('❌ 解压异常', 'error');
            }
        }

        function getPanelConfig(isSource) {
            return {
                serverSelect: isSource ? 'sourceServer' : 'targetServer',
                showHiddenCheckbox: isSource ? 'sourceShowHidden' : 'targetShowHidden',
                containerId: isSource ? 'sourceFileBrowser' : 'targetFileBrowser'
            };
        }

        function resetBrowseState(isSource, path) {
            const state = isSource ? browseState.source : browseState.target;
            state.path = path;
            state.offset = 0;
            state.total = 0;
            state.hasMore = false;
            state.loadedCount = 0;
            state.virtualOffset = 0;
            state.lastScrollTop = 0;
            state.rowHeight = 0;
            if (state.jumpTimer) {
                clearTimeout(state.jumpTimer);
                state.jumpTimer = null;
            }
            state.pendingJump = null;
            state.requestToken = `${Date.now()}_${Math.random()}`;
            if (state.controller) {
                try { state.controller.abort(); } catch (_) {}
                state.controller = null;
            }
        }

        function clearSelectionsForPanel(isSource) {
            if (isSource) {
                selectedSourceFiles = [];
            } else {
                selectedTargetFiles = [];
            }
            const containerId = isSource ? 'sourceFileBrowser' : 'targetFileBrowser';
            document.querySelectorAll(`#${containerId} .file-item.selected`).forEach(el => el.classList.remove('selected'));
        }

        function updateFileCountDisplay(isSource, loaded, total) {
            const el = document.getElementById(isSource ? 'sourceFileCount' : 'targetFileCount');
            if (!el) return;
            if (total) {
                el.textContent = `已加载 ${loaded}/${total} 项`;
            } else {
                el.textContent = `共 ${loaded} 项`;
            }
        }

        function showLoading(containerId) {
            const container = document.getElementById(containerId);
            if (container) {
                container.innerHTML = '<div class="text-center text-muted p-2" style="font-size: 0.8rem;">加载中...</div>';
            }
        }

        function getActivePath(isSource) {
            return isSource ? currentSourcePath : currentTargetPath;
        }

        async function loadDirectory(type, targetPath, options = {}) {
            const isSource = type === 'source';
            const state = isSource ? browseState.source : browseState.target;
            const { serverSelect, showHiddenCheckbox, containerId } = getPanelConfig(isSource);
            const server = document.getElementById(serverSelect).value;
            const showHidden = document.getElementById(showHiddenCheckbox).checked;
            const forceRefresh = options.forceRefresh === true;
            const silent = options.silent === true;
            const pinToBottom = options.pinToBottom === true;
            const skipAutoFill = options.skipAutoFill === true;
            const isReset = options.reset === true || state.path !== targetPath || state.loadedCount === 0;

            if (!server) {
                await showAlertDialog(isSource ? '请先选择源服务器' : '请先选择目标服务器');
                return;
            }

            if (isReset) {
                resetBrowseState(isSource, targetPath);
                clearSelectionsForPanel(isSource);
                if (!silent) {
                    showLoading(containerId);
                }
            } else {
                if (state.loading || !state.hasMore) {
                    return;
                }
            }

            if (Number.isInteger(options.virtualOffset)) {
                state.virtualOffset = Math.max(0, options.virtualOffset);
            } else if (isReset) {
                state.virtualOffset = 0;
            }

            const requestOffset = Number.isInteger(options.overrideOffset) ? Math.max(0, options.overrideOffset) : state.offset;

            const controller = new AbortController();
            if (state.controller) {
                try { state.controller.abort(); } catch (_) {}
            }
            state.controller = controller;
            state.loading = true;
            const requestToken = `${Date.now()}_${Math.random()}`;
            state.requestToken = requestToken;


            if (forceRefresh) {
                invalidatePreviewCacheUnderDir(server, targetPath);
            }

            const params = new URLSearchParams({
                path: targetPath,
                show_hidden: showHidden,
                offset: requestOffset,
                limit: BROWSE_PAGE_SIZE,
                force_refresh: forceRefresh
            });

            try {
                const response = await fetch(`/api/browse/${server}?${params.toString()}`, {
                    signal: controller.signal,
                    cache: forceRefresh ? 'no-cache' : 'default'
                });

                const data = await response.json();


                const activePath = getActivePath(isSource);
                if (state.requestToken !== requestToken || activePath !== targetPath) {
                    return;
                }

                if (data.success) {
                    const pageFiles = data.files || [];
                    const total = data.total_count || data.file_count || 0;
                    const startIndex = requestOffset;

                    state.total = total;
                    state.loadedCount = data.loaded_count || (state.loadedCount + pageFiles.length);
                    state.hasMore = data.has_more;
                    state.offset = data.next_offset ?? state.loadedCount;

                    if (isReset) {
                        state.fullItems = [];
                        state.fullOffset = 0;
                        state.fullHasMore = false;
                    }
                    state.fullItems = (state.fullItems || []).concat(pageFiles);
                    state.fullOffset = data.next_offset ?? state.loadedCount;
                    state.fullHasMore = data.has_more;

                    displayFiles(containerId, pageFiles, targetPath, isSource, {
                        append: !isReset && startIndex > 0,
                        totalCount: total,
                        loadedCount: state.loadedCount,
                        startIndex: startIndex,
                        virtualOffset: state.virtualOffset,
                        preserveScrollTop: options.preserveScrollTop
                    });
                    updateFileCountDisplay(isSource, state.loadedCount, total);
                    state.path = targetPath;
                    if (server && targetPath) {
                        saveClientPath(isSource ? 'source' : 'target', server, targetPath);
                    }


                    const container = document.getElementById(containerId);
                    if (state.hasMore && container && !skipAutoFill) {
                        const items = container.querySelectorAll('.file-item');
                        if (items.length) {
                            const last = items[items.length - 1];
                            const loadedBottom = last.offsetTop + last.offsetHeight;
                            const viewportBottom = container.scrollTop + container.clientHeight;
                            if (viewportBottom >= loadedBottom - 120) {
                                setTimeout(() => handleScrollLoadMore(isSource), 0);
                            }
                        }
                    }
                } else {
                    state.hasMore = false;
                    showErrorState(containerId, '浏览失败: ' + data.error);
                    addLogError(`❌ 浏览${isSource ? '源' : '目标'}目录失败: ${data.error}`);
                }
            } catch (error) {
                if (error.name !== 'AbortError') {
                    console.error('Error:', error);
                    showErrorState(containerId, '浏览失败: ' + error.message);
                    addLogError(`❌ 浏览${isSource ? '源' : '目标'}目录出错: ${error}`);
                }
                state.hasMore = false;
            } finally {
                state.loading = false;
            }
        }


        function browseSource() {
            loadDirectory('source', currentSourcePath, { reset: true });
        }

        function refreshSource() {
            loadDirectory('source', currentSourcePath, { reset: true, forceRefresh: true });
        }

        async function browseSourceAsync() {
            return loadDirectory('source', currentSourcePath, { reset: true });
        }

        async function browseSourceInstant(targetPath) {
            currentSourcePath = targetPath;
            return loadDirectory('source', targetPath, { reset: true });
        }

        async function refreshSourceAsync(options = {}) {
            return loadDirectory('source', currentSourcePath, {
                reset: true,
                forceRefresh: true,
                ...options
            });
        }

        function browseTarget() {
            loadDirectory('target', currentTargetPath, { reset: true });
        }

        function refreshTarget() {
            loadDirectory('target', currentTargetPath, { reset: true, forceRefresh: true });
        }

        async function browseTargetAsync() {
            return loadDirectory('target', currentTargetPath, { reset: true });
        }

        async function browseTargetInstant(targetPath) {
            currentTargetPath = targetPath;
            return loadDirectory('target', targetPath, { reset: true });
        }

        async function saveClientPath(panel, server, path) {
            if (!panel || !server || !path) return;
            updateRememberedCache(panel, server, path);
            try {
                await fetch('/api/client_path/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ panel, server, path })
                });
            } catch (_) {

            }
        }

        async function refreshTargetAsync(options = {}) {
            return loadDirectory('target', currentTargetPath, {
                reset: true,
                forceRefresh: true,
                ...options
            });
        }


        function getLoadedBottom(container) {
            const items = container.querySelectorAll('.file-item');
            if (!items.length) return 0;
            const last = items[items.length - 1];
            return last.offsetTop + last.offsetHeight;
        }

        function getBackRowHeight(container) {
            const backRow = container.querySelector('.file-item.back-row');
            return backRow ? backRow.offsetHeight : 0;
        }

        function scheduleJumpLoad(isSource, targetIndex, scrollTop) {
            const state = isSource ? browseState.source : browseState.target;
            if (!state.path) return;
            const pageStart = Math.floor(targetIndex / BROWSE_PAGE_SIZE) * BROWSE_PAGE_SIZE;
            const currentStart = state.virtualOffset || 0;
            if (pageStart === currentStart && state.loadedCount > 0) return;

            state.pendingJump = { pageStart, scrollTop };
            if (state.jumpTimer) {
                clearTimeout(state.jumpTimer);
            }
            state.jumpTimer = setTimeout(() => {
                const pending = state.pendingJump;
                state.pendingJump = null;
                state.jumpTimer = null;
                if (!pending) return;
                loadDirectory(isSource ? 'source' : 'target', state.path || getActivePath(isSource), {
                    reset: true,
                    silent: true,
                    overrideOffset: pending.pageStart,
                    virtualOffset: pending.pageStart,
                    preserveScrollTop: pending.scrollTop,
                    skipAutoFill: true
                });
            }, 120);
        }

        function handleScrollLoadMore(isSource) {
            const containerId = isSource ? 'sourceFileBrowser' : 'targetFileBrowser';
            const container = document.getElementById(containerId);
            const state = isSource ? browseState.source : browseState.target;
            if (!container || state.loading) return;

            const scrollTop = container.scrollTop;
            state.lastScrollTop = scrollTop;

            const loadedBottom = getLoadedBottom(container);
            const viewportBottom = scrollTop + container.clientHeight;

            const total = state.total || 0;
            const rowHeight = state.rowHeight || 28;
            const backRowHeight = getBackRowHeight(container);
            const effectiveScrollTop = Math.max(0, scrollTop - backRowHeight);
            const maxIndex = total > 0 ? Math.max(0, total - 1) : 0;
            const targetIndex = total > 0 ? Math.min(maxIndex, Math.floor(effectiveScrollTop / rowHeight)) : 0;

            const loadedItems = container.querySelectorAll('.file-item.selectable').length;
            const currentStart = state.virtualOffset || 0;
            const currentEnd = currentStart + loadedItems;
            const jumpMargin = Math.max(100, Math.floor(BROWSE_PAGE_SIZE / 2));

            if (total > 0 && rowHeight > 0) {
                const outsideRange = targetIndex < (currentStart - jumpMargin) || targetIndex >= (currentEnd + jumpMargin);
                if (outsideRange) {
                    scheduleJumpLoad(isSource, targetIndex, scrollTop);
                    return;
                }
            }

            if (state.hasMore && viewportBottom >= loadedBottom - 120) {
                loadDirectory(isSource ? 'source' : 'target', state.path || getActivePath(isSource), {
                    reset: false
                });
            }
        }


        function toggleHiddenFiles(type) {
            if (type === 'source') {
                const server = document.getElementById('sourceServer').value;
                if (server && currentSourcePath) {
                    browseSource();
                }
            } else if (type === 'target') {
                const server = document.getElementById('targetServer').value;
                if (server && currentTargetPath) {
                    browseTarget();
                }
            }
        }


        function sortFilesWinSCPStyle(files) {
            return files.sort((a, b) => {

                if (a.is_directory && !b.is_directory) {
                    return -1;
                }
                if (!a.is_directory && b.is_directory) {
                    return 1;
                }


                const nameA = a.name.toLowerCase();
                const nameB = b.name.toLowerCase();


                return nameA.localeCompare(nameB, undefined, {
                    numeric: true,
                    sensitivity: 'base'
                });
            });
        }


        function displayFiles(containerId, files, currentPath, isSource, options = {}) {
            const container = document.getElementById(containerId);
            if (!container) return;

            const append = options.append === true;
            const totalCount = options.totalCount ?? null;
            const loadedCount = options.loadedCount ?? null;
            const startIndex = options.startIndex || 0;
            const virtualOffset = Number.isInteger(options.virtualOffset) ? options.virtualOffset : 0;
            const preserveScrollTop = Number.isFinite(options.preserveScrollTop)
                ? options.preserveScrollTop
                : (append ? container.scrollTop : null);

            const pathChanged = container.dataset.currentPath !== currentPath;
            const shouldReset = !append || pathChanged;

            if (shouldReset) {
                container.dataset.currentPath = currentPath;
                container.innerHTML = '';
                updatePathNavigation(currentPath, isSource);

                if (currentPath !== '/') {
                    const parentPath = getParentPath(currentPath);
                    const backRow = document.createElement('div');
                    backRow.className = 'file-item back-row';
                    backRow.title = '双击返回上级目录';
                    backRow.dataset.path = parentPath;
                    backRow.dataset.isDirectory = 'true';
                    backRow.ondblclick = () => navigateTo(containerId, parentPath, isSource);
                    backRow.innerHTML = `
                        <i class="bi bi-arrow-up-circle text-primary"></i>
                        <div class="file-info">
                            <span class="file-name">..</span>
                            <span class="file-details" style="font-size: 0.75rem; color: #6c757d;">双击返回上级</span>
                        </div>
                    `;
                    container.appendChild(backRow);
                    attachRowDropHandlers(backRow, isSource);
                }
            } else {

                container.querySelectorAll('.file-browser-spacer').forEach(el => el.remove());
            }

            let topSpacer = null;
            if (virtualOffset > 0) {
                topSpacer = document.createElement('div');
                topSpacer.className = 'file-browser-spacer file-browser-spacer-top';
                const backRow = container.querySelector('.file-item.back-row');
                const firstSelectable = container.querySelector('.file-item.selectable');
                const anchor = backRow ? backRow.nextSibling : firstSelectable;
                if (anchor) {
                    container.insertBefore(topSpacer, anchor);
                } else {
                    container.appendChild(topSpacer);
                }
            }

            const sortedFiles = sortFilesWinSCPStyle([...files]);

            const fragment = document.createDocumentFragment();
            sortedFiles.forEach((file, index) => {
                const icon = file.is_directory ? 'bi-folder-fill text-warning' : 'bi-file-earmark text-info';
                const size = file.is_directory ? '' : formatFileSize(file.size);
                const fileId = `file_${containerId}_${startIndex + index}`;
                const fileIdx = startIndex + index;

                const row = document.createElement('div');
                row.className = 'file-item selectable';
                row.id = fileId;
                row.dataset.path = file.path;
                row.dataset.name = file.name;
                row.dataset.isDirectory = file.is_directory;
                row.dataset.idx = fileIdx;
                row.onmousedown = (event) => handleFileMouseDown(event, file.path, file.name, file.is_directory, fileId, isSource);
                row.ondblclick = function() { handleFileItemDblClick(row, isSource); };
                attachRowDragHandlers(row, isSource);
                attachRowDropHandlers(row, isSource);
                row.innerHTML = `
                    <i class="bi ${icon}"></i>
                    <div class="file-info">
                        <span class="file-name">${file.name}</span>
                        <span class="file-details">${size} ${file.modified}</span>
                    </div>
                `;

                fragment.appendChild(row);
            });

            container.appendChild(fragment);


            const spacer = document.createElement('div');
            spacer.className = 'file-browser-spacer file-browser-spacer-bottom';
            const state = isSource ? browseState.source : browseState.target;
            if (!state.rowHeight) {
                const sampleRow = container.querySelector('.file-item.selectable');
                if (sampleRow) {
                    const rect = sampleRow.getBoundingClientRect();
                    if (rect.height > 0) {
                        state.rowHeight = rect.height;
                    }
                }
            }
            const rowHeight = state.rowHeight || 28;
            let spacerHeight = 20;
            if (typeof totalCount === 'number' && typeof loadedCount === 'number') {
                const remaining = Math.max(0, totalCount - loadedCount);
                spacerHeight = Math.max(20, Math.round(remaining * rowHeight));
            }
            spacer.style.height = `${spacerHeight}px`;
            container.appendChild(spacer);

            if (topSpacer) {
                topSpacer.style.height = `${Math.max(20, Math.round(virtualOffset * rowHeight))}px`;
            }

            const loaded = loadedCount !== null ? loadedCount : container.querySelectorAll('.file-item.selectable').length;
            const total = totalCount !== null ? totalCount : loaded;
            updateFileCountDisplay(isSource, loaded, total);

            if (preserveScrollTop !== null) {
                requestAnimationFrame(() => {
                    const maxScroll = Math.max(0, container.scrollHeight - container.clientHeight);
                    container.scrollTop = Math.min(preserveScrollTop, maxScroll);
                });
            }
        }


        function formatFileSize(bytes) {
            if (bytes === 0) return '';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + sizes[i];
        }


        function navigateTo(containerId, path, isSource) {
            if (isSource) {
                currentSourcePath = path;

                browseSourceInstant(path);
            } else {
                currentTargetPath = path;

                browseTargetInstant(path);
            }
        }


        const DOUBLE_CLICK_CONFIG = {
            timeWindow: 300,
            debounceDelay: 50
        };


        const INTERACTION_CONFIG = {
            immediateVisualFeedback: true,
            removeAllAnimations: true,
            staticHoverEffects: true
        };


        let clickTimer = null;
        let clickCount = 0;

        function handleFileMouseDown(event, path, name, isDirectory, fileId, isSource) {
            if (event.button !== 0) return;

            lastActivePanel = isSource ? 'source' : 'target';


            if (fileId) {

                selectFileImmediate(event, path, name, isDirectory, fileId, isSource);
            }

            clickCount++;

            if (clickCount === 1) {

                clickTimer = setTimeout(() => {

                    clickCount = 0;
                }, DOUBLE_CLICK_CONFIG.timeWindow);
            } else if (clickCount === 2) {

                clearTimeout(clickTimer);
                clickCount = 0;

                if (isDirectory) {

                    console.log(`[双击] 立即进入目录: ${path}`);


                    if (isSource) {
                        currentSourcePath = path;

                        browseSourceInstant(path);
                    } else {
                        currentTargetPath = path;

                        browseTargetInstant(path);
                    }
                }
            }
        }


        function handleDoubleClick(path, isDirectory, isSource) {
            if (!isDirectory) return;


            console.log(`[双击] 立即进入目录: ${path}`);


            if (isSource) {
                currentSourcePath = path;

                browseSourceInstant(path);
            } else {
                currentTargetPath = path;

                browseTargetInstant(path);
            }
        }






        function showErrorState(containerId, errorMessage) {
            const container = document.getElementById(containerId);
            container.innerHTML = `
                <div class="text-center p-4">
                    <div class="text-danger mb-3">
                        <i class="bi bi-exclamation-triangle-fill" style="font-size: 2rem;"></i>
                    </div>
                    <div class="text-danger">${errorMessage}</div>
                    <button class="btn btn-outline-primary btn-sm mt-3" onclick="location.reload()">
                        <i class="bi bi-arrow-clockwise"></i> 重新加载
                    </button>
                </div>
            `;
        }


        function debounce(func, wait) {
            let timeout;
            return function executedFunction(...args) {
                const later = () => {
                    clearTimeout(timeout);
                    func(...args);
                };
                clearTimeout(timeout);
                timeout = setTimeout(later, wait);
            };
        }


        const debouncedHandleDoubleClick = debounce(function(path, isDirectory, isSource) {
            if (isDirectory) {

                if (isSource) {
                    currentSourcePath = path;

                    browseSourceAsync();
                } else {
                    currentTargetPath = path;

                    browseTargetAsync();
                }
            }
        }, DOUBLE_CLICK_CONFIG.debounceDelay);


        function updatePathNavigation(currentPath, isSource) {
            const navId = isSource ? 'sourcePathNav' : 'targetPathNav';
            const displayId = isSource ? 'sourcePathDisplay' : 'targetPathDisplay';
            const nav = document.getElementById(navId);
            const display = document.getElementById(displayId);


            if (display && display.dataset && (display.dataset.editing === 'true' || display.dataset.editing === 'committing')) {
                return;
            }

            const isWindows = currentPath && currentPath.includes(':');

            function buildSegmentLink(label, path) {
                return `<a href="#" class="path-segment" onclick="navigateToPath('${path}', ${isSource})">${label}</a>`;
            }

            function renderSegmentsHtml(segments, leadingSeparator = false) {
                return segments.map((seg, idx) => {
                    const sep = (idx === 0 ? (leadingSeparator ? '<span class="path-separator">/</span>' : '') : '<span class="path-separator">/</span>');
                    const content = seg.html || buildSegmentLink(seg.label, seg.path);
                    return `${sep}${content}`;
                }).join('');
            }

            function schedulePathCollapse(segments, pathValue) {
                if (!display) return;
                if (display._pathCollapseRaf) {
                    cancelAnimationFrame(display._pathCollapseRaf);
                }
                display._pathCollapseRaf = requestAnimationFrame(() => {
                    display._pathCollapseRaf = null;
                    if (!display.isConnected) return;
                    if (display.dataset && (display.dataset.editing === 'true' || display.dataset.editing === 'committing')) return;
                    if ((display.dataset.currentPath || '/') !== pathValue) return;
                    if (display.scrollWidth <= display.clientWidth + 1) {
                        display.dataset.collapsed = '';
                        return;
                    }

                    const totalSegments = segments.length;
                    if (totalSegments <= 2) {
                        display.innerHTML = `<span class="path-display-leading">${renderSegmentsHtml(segments)}</span>`;
                        display.dataset.collapsed = '';
                        return;
                    }

                    const headCount = Math.min(2, totalSegments - 2);
                    const maxTail = Math.max(1, totalSegments - headCount - 1);
                    let tailCount = Math.min(2, maxTail);

                    const buildCollapsedHtml = (hCount, tCount) => {
                        const headSegments = segments.slice(0, hCount);
                        const tailSegments = segments.slice(-tCount);
                        const leadingHtml = renderSegmentsHtml(headSegments);
                        const tailHtml = renderSegmentsHtml(tailSegments, true);
                        return `
                        <span class="path-display-leading">${leadingHtml}<span class="path-separator">/</span><span class="path-ellipsis">…</span></span>
                        <span class="path-display-tail">${tailHtml}</span>
                    `;
                    };

                    let bestHtml = buildCollapsedHtml(headCount, tailCount);
                    display.innerHTML = bestHtml;

                    while (tailCount < maxTail && display.scrollWidth <= display.clientWidth + 1) {
                        const next = tailCount + 1;
                        const candidate = buildCollapsedHtml(headCount, next);
                        display.innerHTML = candidate;
                        if (display.scrollWidth <= display.clientWidth + 1) {
                            tailCount = next;
                            bestHtml = candidate;
                        } else {
                            break;
                        }
                    }

                    display.innerHTML = bestHtml;
                    display.dataset.collapsed = 'true';
                });
            }

            if (currentPath && currentPath !== '/') {
                nav.style.display = 'flex';
                const parts = currentPath.split('/').filter(part => part);
                const segments = [];
                let html = '';

                if (isWindows) {

                    const drive = parts[0];
                    const driveLetter = (drive || '').replace(':', '').toUpperCase();
                    const driveLabel = driveLetter ? `${driveLetter}盘` : drive;


                    const drives = (isSource ? windowsDrivesSource : windowsDrivesTarget) || [];
                    if (drives.length > 0) {
                        const currentDriveMeta = drives.find(d => {
                            const letterRaw = (d.letter || '').toUpperCase();
                            const letter = letterRaw.endsWith(':') ? letterRaw : (letterRaw + ':');
                            return letter === drive.toUpperCase();
                        });
                        const driveIcon = currentDriveMeta && currentDriveMeta.type === 'network'
                            ? 'bi-globe2'
                            : 'bi-hdd-fill';
                        const driveMenuItems = drives.map(d => {
                            const letterRaw = (d.letter || '').toUpperCase();
                            const letter = letterRaw.endsWith(':') ? letterRaw : (letterRaw + ':');
                            const iconHtml = d.type === 'network'
                                ? '<i class="bi bi-globe2 me-2 text-secondary"></i>'
                                : '<i class="bi bi-hdd-fill me-2 text-secondary"></i>';
                            const active = letter === drive.toUpperCase() ? ' active' : '';
                            return `<li><a class="dropdown-item${active}" href="#" onclick="switchWindowsDrive('${letter}', ${isSource}); return false;">${iconHtml}${d.name}</a></li>`;
                        }).join('');
                        segments.push({
                            html: `
                                <div class="dropdown d-inline-block">
                                    <a href="#" class="path-segment dropdown-toggle drive-toggle" data-bs-toggle="dropdown" onclick="event.preventDefault();">
                                        <span class="drive-pill">
                                            <i class="bi ${driveIcon} drive-icon"></i>
                                            <span class="drive-letter">${driveLetter || drive}</span>
                                            <span class="drive-suffix">盘</span>
                                        </span>
                                        <i class="bi bi-chevron-down drive-caret"></i>
                                    </a>
                                    <ul class="dropdown-menu">${driveMenuItems}</ul>
                                </div>
                            `
                        });
                    } else {

                        segments.push({ label: driveLabel, path: `${drive}/` });
                    }

                    let buildPath = drive;

                    parts.slice(1).forEach(part => {
                        buildPath += '/' + part;
                        segments.push({ label: part, path: buildPath });
                    });
                } else {

                    let buildPath = '';
                    segments.push({ label: '根目录', path: '/' });
                    parts.forEach(part => {
                        buildPath += '/' + part;
                        segments.push({ label: part, path: buildPath });
                    });
                }

                display.innerHTML = `<span class="path-display-leading">${renderSegmentsHtml(segments)}</span>`;
                display.dataset.collapsed = '';
                const pathValue = currentPath || '/';
                display.dataset.currentPath = pathValue;
                display.title = pathValue;
                schedulePathCollapse(segments, pathValue);
            } else {
                nav.style.display = 'flex';
                display.innerHTML = '<span class="path-display-leading"><span class="path-segment">根目录</span></span>';
                const pathValue = currentPath || '/';
                if (display) {
                    display.dataset.currentPath = pathValue;
                    display.title = pathValue;
                    display.dataset.collapsed = '';
                }
            }


            if (display) {
                display.dataset.currentPath = currentPath || '/';
                display.title = currentPath || '/';
            }
        }

        function maybeStartPathInlineEdit(event, isSource) {

            if (event.target.closest('button') || event.target.closest('a') || event.target.closest('.bi')) return;
            startPathInlineEdit(isSource);
        }

        function startPathInlineEdit(isSource) {
            const displayId = isSource ? 'sourcePathDisplay' : 'targetPathDisplay';
            const display = document.getElementById(displayId);
            if (!display) return;

            const currentPath = display.dataset.currentPath || (isSource ? currentSourcePath : currentTargetPath) || '/';
            display.dataset.editing = 'true';
            display.innerHTML = `<span class="path-inline-editor" contenteditable="true" spellcheck="false" onkeydown="handlePathInlineKey(event, ${isSource})" onblur="cancelPathInlineEdit(${isSource})">${currentPath}</span>`;

            const editor = display.querySelector('.path-inline-editor');
            if (editor) {
                setTimeout(() => selectAllText(editor), 0);
            }
        }

        function selectAllText(el) {
            try {
                const range = document.createRange();
                range.selectNodeContents(el);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
            } catch (_) {}
        }

        function handlePathInlineKey(event, isSource) {
            if (event.key === 'Enter') {
                event.preventDefault();
                const target = event.target;
                let path = (target.textContent || '').trim();
                if (!path) return;
                if (isWindowsPath(path)) {
                    path = path.replace(/\\+/g, '/');
                }
                const display = document.getElementById(isSource ? 'sourcePathDisplay' : 'targetPathDisplay');
                if (display) {
                    display.dataset.editing = 'committing';
                }
                navigateToPath(path, isSource);

                setTimeout(() => {
                    if (display) {
                        display.dataset.editing = '';
                    }
                    updatePathNavigation(isSource ? currentSourcePath : currentTargetPath, isSource);
                }, 0);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                cancelPathInlineEdit(isSource);
            }
        }

        function cancelPathInlineEdit(isSource) {
            const display = document.getElementById(isSource ? 'sourcePathDisplay' : 'targetPathDisplay');
            if (display && display.dataset.editing === 'committing') {
                return;
            }
            if (display) display.dataset.editing = '';
            updatePathNavigation(isSource ? currentSourcePath : currentTargetPath, isSource);
        }


        document.addEventListener('show.bs.dropdown', (event) => {
            const display = event.target.closest('.path-display');
            if (display) display.classList.add('dropdown-open');
        });
        document.addEventListener('hidden.bs.dropdown', (event) => {
            const display = event.target.closest('.path-display');
            if (display) display.classList.remove('dropdown-open');
        });


            function isWindowsPath(p) {
                return !!(p && p.includes(':'));
            }


            function getParentPath(p) {
                if (!p) return '/';
                if (isWindowsPath(p)) {
                    const parts = p.split('/').filter(Boolean);
                    if (parts.length <= 1) {

                        return parts[0] + '/';
                    }
                    const parentParts = parts.slice(0, -1);
                    let parent = parentParts.join('/');
                    if (parentParts.length === 1) {

                        parent += '/';
                    }
                    return parent;
                }

                return p.split('/').slice(0, -1).join('/') || '/';
            }



        function switchWindowsDrive(letter, isSource) {
            const driveLetter = letter.endsWith(':') ? letter : (letter + ':');
            const drivePath = driveLetter + '/';
            if (isSource) {
                currentSourcePath = drivePath;
                browseSourceInstant(currentSourcePath);
                updatePathNavigation(currentSourcePath, true);
            } else {
                currentTargetPath = drivePath;
                browseTargetInstant(currentTargetPath);
                updatePathNavigation(currentTargetPath, false);
            }
        }



        function navigateToPath(path, isSource) {
            if (isSource) {
                currentSourcePath = path;

                browseSourceInstant(path);
            } else {
                currentTargetPath = path;

                browseTargetInstant(path);
            }
        }


        function selectFileImmediate(event, path, name, isDirectory, fileId, isSource) {
            event.stopPropagation();

            const fileElement = document.getElementById(fileId);
            if (!fileElement) return;

            const idx = Number(fileElement.dataset.idx || -1);

            if (!event.shiftKey && !event.ctrlKey && !event.metaKey && fileElement.classList.contains('selected')) {
                lastSelectedIndex[isSource ? 'source' : 'target'] = idx;
                updateSelectionInfo();
                return;
            }

            if (event.shiftKey && (lastSelectedIndex[isSource ? 'source' : 'target'] !== null)) {
                const anchor = lastSelectedIndex[isSource ? 'source' : 'target'];
                selectRange(isSource, anchor, idx);
            } else if (event.ctrlKey || event.metaKey) {
                const arr = isSource ? selectedSourceFiles : selectedTargetFiles;
                if (fileElement.classList.contains('selected')) {
                    const newArr = arr.filter(f => f.path !== path);
                    if (isSource) selectedSourceFiles = newArr; else selectedTargetFiles = newArr;
                    fileElement.classList.remove('selected');
                } else {
                    arr.push({path, name, is_directory: isDirectory});
                    fileElement.classList.add('selected');
                }
                lastSelectedIndex[isSource ? 'source' : 'target'] = idx;
            } else {

                clearAllSelections();
                fileElement.classList.add('selected');
                if (isSource) {
                    selectedSourceFiles = [{path, name, is_directory: isDirectory}];
                } else {
                    selectedTargetFiles = [{path, name, is_directory: isDirectory}];
                }
                lastSelectedIndex[isSource ? 'source' : 'target'] = idx;
            }

            updateSelectionInfo();
        }


        function selectFile(event, path, name, isDirectory, fileId) {

            const isSource = fileId && fileId.indexOf('sourceFileBrowser') !== -1;
            selectFileImmediate(event, path, name, isDirectory, fileId, isSource);
        }

        function getFileNodes(isSource) {
            const containerId = isSource ? 'sourceFileBrowser' : 'targetFileBrowser';
            return Array.from(document.querySelectorAll(`#${containerId} .file-item.selectable`));
        }

        async function ensureAllItemsLoaded(isSource) {
            const state = isSource ? browseState.source : browseState.target;
            if (state.loadingAllPromise) {
                return state.loadingAllPromise;
            }
            const { serverSelect, showHiddenCheckbox } = getPanelConfig(isSource);
            const server = document.getElementById(serverSelect).value;
            const showHidden = document.getElementById(showHiddenCheckbox).checked;
            if (!server || !state.path) return state.fullItems || [];


            const loadedCount = state.fullItems ? state.fullItems.length : 0;
            if (!state.hasMore && loadedCount >= (state.total || loadedCount)) {
                return state.fullItems || [];
            }

            state.loadingAllPromise = (async () => {
                let localOffset = loadedCount;
                while (true) {
                    const params = new URLSearchParams({
                        path: state.path,
                        show_hidden: showHidden,
                        offset: localOffset,
                        limit: BROWSE_PAGE_SIZE_MAX
                    });
                    const resp = await fetch(`/api/browse/${server}?${params.toString()}`, { cache: 'no-cache' });
                    const data = await resp.json();
                    if (!data.success) break;
                    const pageFiles = data.files || [];
                    state.fullItems = (state.fullItems || []).concat(pageFiles);
                    state.total = data.total_count || data.file_count || state.total || 0;
                    state.fullOffset = data.next_offset ?? (localOffset + pageFiles.length);
                    state.fullHasMore = data.has_more;
                    state.loadedCount = Math.max(state.loadedCount || 0, state.fullOffset || 0);
                    localOffset = state.fullOffset || (localOffset + pageFiles.length);
                    if (!data.has_more) break;
                }
                return state.fullItems || [];
            })();

            try {
                return await state.loadingAllPromise;
            } finally {
                state.loadingAllPromise = null;
            }
        }

        function renderAllItems(isSource, items) {
            const state = isSource ? browseState.source : browseState.target;
            const { containerId } = getPanelConfig(isSource);
            const path = getActivePath(isSource);
            const total = state.total || items.length;
            state.virtualOffset = 0;
            state.loadedCount = items.length;
            state.offset = items.length;
            state.hasMore = false;
            displayFiles(containerId, items, path, isSource, {
                append: false,
                totalCount: total,
                loadedCount: state.loadedCount,
                startIndex: 0
            });
            updateFileCountDisplay(isSource, state.loadedCount, total);
        }

        async function quickLocateInPanel(isSource, event) {
            if (event && typeof event.stopPropagation === 'function') {
                event.stopPropagation();
            }
            const state = isSource ? browseState.source : browseState.target;
            const { serverSelect, showHiddenCheckbox, containerId } = getPanelConfig(isSource);
            const server = document.getElementById(serverSelect).value;
            if (!server || !state.path) {
                addLogWarning('⚠️ 请先选择服务器并进入目录');
                return;
            }

            const input = await showPromptDialog('输入要查找的文件/文件夹名称关键字', {
                title: '快速查找',
                placeholder: '文件/文件夹关键字'
            });
            if (input === null) return;
            const rawKeyword = String(input || '').trim();
            const keyword = rawKeyword.toLowerCase();
            if (!keyword) {
                addLogWarning('⚠️ 请输入有效的关键字');
                return;
            }

            const locateRow = (row, match) => {
                if (!row) return false;
                const fakeEvent = {
                    stopPropagation() {},
                    shiftKey: false,
                    ctrlKey: false,
                    metaKey: false
                };
                selectFileImmediate(fakeEvent, match.path, match.name, match.is_directory, row.id, isSource);
                const container = document.getElementById(containerId);
                if (container) {
                    const prevBehavior = container.style.scrollBehavior;
                    container.style.scrollBehavior = 'auto';
                    const targetTop = row.offsetTop - (container.clientHeight / 2) + (row.offsetHeight / 2);
                    container.scrollTop = Math.max(0, targetTop);
                    requestAnimationFrame(() => {
                        container.style.scrollBehavior = prevBehavior || '';
                    });
                }
                return true;
            };


            const localNodes = getFileNodes(isSource);
            const localRow = localNodes.find(node => {
                const name = String(node.dataset.name || '').toLowerCase();
                return name.includes(keyword);
            });
            if (localRow) {
                const localMatch = {
                    name: localRow.dataset.name || '',
                    path: localRow.dataset.path || '',
                    is_directory: String(localRow.dataset.isDirectory).toLowerCase() === 'true'
                };
                if (locateRow(localRow, localMatch)) {
                    addLogInfo(`✅ 已定位到: ${localMatch.name}`);
                    return;
                }
            }

            addLogInfo('🔎 正在快速查找，请稍候...');
            const showHidden = document.getElementById(showHiddenCheckbox).checked;
            const params = new URLSearchParams({
                path: state.path,
                keyword: rawKeyword,
                show_hidden: showHidden
            });

            let data;
            try {
                const resp = await fetch(`/api/quick_search/${server}?${params.toString()}`, { cache: 'no-cache' });
                data = await resp.json();
            } catch (err) {
                addLogError('❌ 查找失败: ' + (err.message || err));
                return;
            }

            if (!data || !data.success) {
                addLogError(`❌ 查找失败: ${data && data.error ? data.error : '未知错误'}`);
                return;
            }

            const match = data.match;
            if (!match || !match.path) {
                addLogWarning(`⚠️ 未找到包含 “${rawKeyword}” 的文件或文件夹`);
                return;
            }

            const rowInView = getFileNodes(isSource).find(node => node.dataset.path === match.path);
            if (rowInView && locateRow(rowInView, match)) {
                addLogInfo(`✅ 已定位到: ${match.name}`);
                return;
            }

            const index = typeof data.index === 'number' ? data.index : -1;
            if (index < 0) {
                addLogWarning('⚠️ 已找到目标，但无法定位到界面项');
                return;
            }

            const pageStart = Math.floor(index / BROWSE_PAGE_SIZE) * BROWSE_PAGE_SIZE;
            const pageParams = new URLSearchParams({
                path: state.path,
                show_hidden: showHidden,
                offset: pageStart,
                limit: BROWSE_PAGE_SIZE
            });

            try {
                const resp = await fetch(`/api/browse/${server}?${pageParams.toString()}`, { cache: 'no-cache' });
                const pageData = await resp.json();
                if (!pageData.success) {
                    addLogError(`❌ 定位失败: ${pageData.error || '未知错误'}`);
                    return;
                }

                state.path = pageData.path || state.path;
                state.offset = pageData.next_offset ?? (pageStart + (pageData.files || []).length);
                state.loadedCount = pageData.loaded_count ?? state.offset;
                state.total = pageData.total_count || pageData.file_count || 0;
                state.hasMore = pageData.has_more;
                state.virtualOffset = pageStart;
                state.fullItems = pageData.files || [];
                state.fullOffset = state.offset;
                state.fullHasMore = pageData.has_more;

                displayFiles(containerId, pageData.files || [], state.path, isSource, {
                    append: false,
                    totalCount: state.total,
                    loadedCount: state.loadedCount,
                    startIndex: pageStart,
                    virtualOffset: pageStart
                });

                const rowAfter = getFileNodes(isSource).find(node => node.dataset.path === match.path);
                if (rowAfter && locateRow(rowAfter, match)) {
                    addLogInfo(`✅ 已定位到: ${match.name}`);
                } else {
                    addLogWarning('⚠️ 已找到目标，但未能定位到界面项');
                }
            } catch (err) {
                addLogError('❌ 定位失败: ' + (err.message || err));
            }
        }


        function clearAllSelections() {
            selectedSourceFiles = [];
            selectedTargetFiles = [];
            document.querySelectorAll('.file-item.selected').forEach(item => {
                item.classList.remove('selected');
            });
            lastSelectedIndex.source = null;
            lastSelectedIndex.target = null;
        }

        function getCurrentTransferMode() {
            const modeRadio = document.querySelector('input[name="transferMode"]:checked');
            return modeRadio ? modeRadio.value : (currentTransferMode || 'copy');
        }

        function ensureDragSelection(row, isSource) {
            if (!row || row.classList.contains('selected')) return;
            clearAllSelections();
            row.classList.add('selected');
            const item = {
                path: row.dataset.path,
                name: row.dataset.name,
                is_directory: String(row.dataset.isDirectory).toLowerCase() === 'true'
            };
            if (isSource) {
                selectedSourceFiles = [item];
                lastSelectedIndex.source = Number(row.dataset.idx || 0);
            } else {
                selectedTargetFiles = [item];
                lastSelectedIndex.target = Number(row.dataset.idx || 0);
            }
            updateSelectionInfo();
        }

        function attachRowDragHandlers(row, isSource) {
            if (!row || row.dataset.dragBound === 'true') return;
            if (row.classList.contains('temp-new') || row.classList.contains('temp-new-file')) return;
            row.draggable = true;
            row.dataset.dragBound = 'true';
            row.addEventListener('dragstart', (event) => handleRowDragStart(event, isSource));
            row.addEventListener('dragend', handleRowDragEnd);
        }

        function _getDragPayload(event) {
            let payload = dragTransferPayload;
            if (!payload && event && event.dataTransfer) {
                try {
                    const raw = event.dataTransfer.getData(DRAG_TRANSFER_TYPE);
                    if (raw) payload = JSON.parse(raw);
                } catch (_) {}
            }
            if (!payload || !Array.isArray(payload.files) || payload.files.length === 0) return null;
            return payload;
        }

        function attachRowDropHandlers(row, isSource) {
            if (!row || row.dataset.dropBound === 'true') return;
            if (row.classList.contains('temp-new') || row.classList.contains('temp-new-file')) return;
            if (String(row.dataset.isDirectory).toLowerCase() !== 'true') return;

            row.dataset.dropBound = 'true';
            const canHandle = (e) => {
                const types = e && e.dataTransfer ? Array.from(e.dataTransfer.types || []) : [];
                return dragTransferPayload !== null || types.includes(DRAG_TRANSFER_TYPE);
            };

            row.addEventListener('dragenter', (e) => {
                if (!canHandle(e)) return;
                e.preventDefault();
                e.stopPropagation();
                row.classList.add('drag-hover');
            });
            row.addEventListener('dragover', (e) => {
                if (!canHandle(e)) return;
                e.preventDefault();
                e.stopPropagation();
                e.dataTransfer.dropEffect = getCurrentTransferMode() === 'move' ? 'move' : 'copy';
            });
            row.addEventListener('dragleave', (e) => {
                if (e.relatedTarget && row.contains(e.relatedTarget)) return;
                row.classList.remove('drag-hover');
            });
            row.addEventListener('drop', async (e) => {
                if (!canHandle(e)) return;
                e.preventDefault();
                e.stopPropagation();
                row.classList.remove('drag-hover');

                const payload = _getDragPayload(e);
                if (!payload) return;

                const fromPanel = payload.panel;
                const toPanel = isSource ? 'source' : 'target';
                const targetPath = row.dataset.path || '';
                if (!targetPath) return;

                const hasConflict = payload.files.some(file =>
                    file.path === targetPath || targetPath.startsWith(file.path + '/')
                );
                if (hasConflict) {
                    showToast('⚠️ 目标目录不能与源相同或为其子目录', 'warning');
                    return;
                }

                const samePanel = fromPanel === toPanel;
                const mode = samePanel ? 'move' : getCurrentTransferMode();
                const sourceServer = _getPanelServer(fromPanel);
                const targetServer = _getPanelServer(toPanel);
                if (!sourceServer || !targetServer) {
                    showToast('⚠️ 请先选择服务器', 'warning');
                    return;
                }

                let direction = (toPanel === 'target') ? 'ltr' : 'rtl';
                if (fromPanel === 'source' && toPanel === 'target') direction = 'ltr';
                if (fromPanel === 'target' && toPanel === 'source') direction = 'rtl';

                let refreshOverride = null;
                if (samePanel) {
                    refreshOverride = {
                        refreshSource: toPanel === 'source',
                        refreshTarget: toPanel === 'target'
                    };
                }

                await startTransferWithParams(
                    sourceServer,
                    targetServer,
                    targetPath,
                    payload.files,
                    mode,
                    direction,
                    refreshOverride,
                    samePanel
                );
            });
        }

        function buildDragPayload(isSource) {
            const files = _cloneTransferFiles(isSource ? selectedSourceFiles : selectedTargetFiles);
            return { panel: isSource ? 'source' : 'target', files, mode: getCurrentTransferMode() };
        }

        function handleRowDragStart(event, isSource) {
            const row = event.currentTarget;
            if (!row || row.dataset.editing === 'true') {
                event.preventDefault();
                return;
            }
            ensureDragSelection(row, isSource);
            const payload = buildDragPayload(isSource);
            if (!payload.files || payload.files.length === 0) {
                event.preventDefault();
                return;
            }

            event.dataTransfer.effectAllowed = 'copyMove';
            event.dataTransfer.setData(DRAG_TRANSFER_TYPE, JSON.stringify(payload));
            const mode = payload.mode === 'move' ? 'move' : 'copy';
            event.dataTransfer.setData('text/plain', `${payload.files.length}项${mode === 'move' ? '剪切' : '复制'}`);
            dragTransferPayload = payload;
            const ghost = getDragGhostImage();
            if (ghost && event.dataTransfer.setDragImage) {
                event.dataTransfer.setDragImage(ghost, 0, 0);
            }
        }

        function handleRowDragEnd() {
            dragTransferPayload = null;
            document.querySelectorAll('.file-item.drag-hover').forEach(el => el.classList.remove('drag-hover'));
            document.querySelectorAll('.file-browser.drag-target').forEach(el => el.classList.remove('drag-target'));
        }

        function setupDragAndDrop() {
            const source = document.getElementById('sourceFileBrowser');
            const target = document.getElementById('targetFileBrowser');
            const bind = (container, isSourcePanel) => {
                if (!container) return;
                const canHandle = (e) => {
                    const types = e.dataTransfer ? Array.from(e.dataTransfer.types || []) : [];
                    return dragTransferPayload !== null || types.includes(DRAG_TRANSFER_TYPE);
                };
                container.addEventListener('dragenter', (e) => {
                    if (!canHandle(e)) return;
                    e.preventDefault();
                });
                container.addEventListener('dragover', (e) => {
                    if (!canHandle(e)) return;
                    e.preventDefault();
                    e.dataTransfer.dropEffect = getCurrentTransferMode() === 'move' ? 'move' : 'copy';
                });
                container.addEventListener('dragleave', (e) => {
                    if (e.relatedTarget && container.contains(e.relatedTarget)) return;
                });
                container.addEventListener('drop', async (e) => {
                    if (!canHandle(e)) return;
                    e.preventDefault();
                    let payload = dragTransferPayload;
                    if (!payload) {
                        try { payload = JSON.parse(e.dataTransfer.getData(DRAG_TRANSFER_TYPE)); } catch (_) {}
                    }
                    if (!payload || !Array.isArray(payload.files) || payload.files.length === 0) return;

                    const fromPanel = payload.panel;
                    const toPanel = isSourcePanel ? 'source' : 'target';
                    if (fromPanel === toPanel) return;

                    const mode = getCurrentTransferMode();
                    const sourceServer = fromPanel === 'source'
                        ? document.getElementById('sourceServer').value
                        : document.getElementById('targetServer').value;
                    const targetServer = fromPanel === 'source'
                        ? document.getElementById('targetServer').value
                        : document.getElementById('sourceServer').value;
                    const targetPath = isSourcePanel ? currentSourcePath : currentTargetPath;
                    const direction = fromPanel === 'source' ? 'ltr' : 'rtl';

                    await startTransferWithParams(sourceServer, targetServer, targetPath, payload.files, mode, direction);
                });
            };
            bind(source, true);
            bind(target, false);
        }

        function selectAll(isSource) {
            ensureAllItemsLoaded(isSource).then(allItems => {
                renderAllItems(isSource, allItems || []);
                const nodes = getFileNodes(isSource);
                nodes.forEach(node => node.classList.add('selected'));
                const selected = (allItems || []).map((f) => ({
                    path: f.path,
                    name: f.name,
                    is_directory: f.is_directory
                }));
                if (isSource) {
                    selectedSourceFiles = selected;
                    lastSelectedIndex.source = selected.length ? selected.length - 1 : null;
                } else {
                    selectedTargetFiles = selected;
                    lastSelectedIndex.target = selected.length ? selected.length - 1 : null;
                }
                updateSelectionInfo();
            });
        }


        function updateSelectionInfo() {
            if (selectedSourceFiles.length > 0 && selectedTargetFiles.length > 0) {
                addLogWarning('⚠️ 左右两侧同时选择了项目，请只在一侧选择以确定方向');
            }

            function renderSelectedInfo(isSource, selectedArr) {
                const el = document.getElementById(isSource ? 'sourceSelectedInfo' : 'targetSelectedInfo');
                if (!el) return;
                if (!selectedArr || selectedArr.length === 0) {
                    el.style.display = 'none';
                    el.textContent = '';
                    return;
                }
                const fileCount = selectedArr.filter(it => !it.is_directory).length;
                const dirCount = selectedArr.filter(it => it.is_directory).length;
                const parts = [];
                if (fileCount > 0) parts.push(`${fileCount} 文件`);
                if (dirCount > 0) parts.push(`${dirCount} 文件夹`);
                el.textContent = `已选中：${parts.join('，')}`;
                el.style.display = 'inline';
            }

            renderSelectedInfo(true, selectedSourceFiles);
            renderSelectedInfo(false, selectedTargetFiles);
        }




        function selectRange(isSource, startIdx, endIdx) {
            const nodes = getFileNodes(isSource);
            const min = Math.min(startIdx, endIdx);
            const max = Math.max(startIdx, endIdx);
            const selected = [];
            nodes.forEach(node => {
                const idx = Number(node.dataset.idx || -1);
                if (idx >= min && idx <= max) {
                    node.classList.add('selected');
                    selected.push({
                        path: node.dataset.path,
                        name: node.dataset.name,
                        is_directory: String(node.dataset.isDirectory).toLowerCase() === 'true'
                    });
                } else {
                    node.classList.remove('selected');
                }
            });
            if (isSource) {
                selectedSourceFiles = selected;
            } else {
                selectedTargetFiles = selected;
            }
        }


        async function startTransfer() {
            const sourceServerLeft = document.getElementById('sourceServer').value;
            const targetServerRight = document.getElementById('targetServer').value;
            const leftSelected = selectedSourceFiles.length > 0;
            const rightSelected = selectedTargetFiles.length > 0;


            const modeRadio = document.querySelector('input[name="transferMode"]:checked');
            const mode = modeRadio ? modeRadio.value : 'copy';
            currentTransferMode = mode;

            const fastSSH = true;
            const parallelTransfer = true;


            if (currentTransferMode === 'move') {
                const ok = await showConfirmDialog('当前选择的是「剪切」模式，源文件将被删除。是否继续？', {
                    title: '剪切确认',
                    danger: true
                });
                if (!ok) {
                    return;
                }
            }


            if (leftSelected && rightSelected) {
                addLogWarning('⚠️ 左右两侧同时选择了项目，请只在一侧选择以确定传输方向');
                await showAlertDialog('左右两侧同时选择了项目，请只在一侧选择以确定传输方向');
                return;
            }
            if (!leftSelected && !rightSelected) {
                addLogWarning('⚠️ 请选择要传输的文件或文件夹');
                await showAlertDialog('请选择要传输的文件或文件夹');
                return;
            }


            let sourceServer, targetServer, targetPath, sourceFiles;
            if (leftSelected) {

                sourceServer = sourceServerLeft;
                targetServer = targetServerRight;
                targetPath = currentTargetPath;
                sourceFiles = selectedSourceFiles;
                currentTransferDirection = 'ltr';
            } else {

                sourceServer = targetServerRight;
                targetServer = sourceServerLeft;
                targetPath = currentSourcePath;
                sourceFiles = selectedTargetFiles;
                currentTransferDirection = 'rtl';
            }


            if (!sourceServer || !targetServer || !targetPath) {
                addLogWarning('⚠️ 请选择源服务器、目标服务器和目标路径');
                await showAlertDialog('请选择源服务器、目标服务器和目标路径');
                return;
            }


            if (sourceServer === targetServer) {
                const hasConflict = sourceFiles.some(file =>
                    file.path === targetPath || targetPath.startsWith(file.path + '/')
                );
                if (hasConflict) {
                    addLogWarning('⚠️ 源路径和目标路径不能相同或存在包含关系');
                    await showAlertDialog('源路径和目标路径不能相同或存在包含关系');
                    return;
                }
            }

            const sourcePath = leftSelected ? currentSourcePath : currentTargetPath;
            cacheTransferContext(sourceServer, targetServer, sourcePath, targetPath, sourceFiles, mode);


            isTransferring = true;


            document.getElementById('progressContainer').style.display = 'block';
            const startBtn = document.getElementById('startTransferBtn');
            if (startBtn) startBtn.style.display = 'none';
            document.getElementById('cancelTransferBtn').style.display = 'inline-block';

            const fileNames = sourceFiles.map(f => f.name).join(', ');
            const modeText = mode === 'copy' ? '复制' : '移动';
            const sshText = fastSSH ? '(SSH加速)' : '';
            const parallelText = parallelTransfer ? '(立即并行传输)' : '';



            addLogInfo(`📤 源: ${sourceServer} (${sourceFiles.length}项)`);
            addLogInfo(`📥 目标: ${targetServer}:${targetPath}`);
            addLogInfo(`📋 文件: ${fileNames.length > 50 ? fileNames.substring(0, 50) + '...' : fileNames}`);


            socket.emit('start_transfer', {
                source_server: sourceServer,
                source_files: sourceFiles,
                target_server: targetServer,
                target_path: targetPath,
                mode: mode,
                fast_ssh: fastSSH,
                parallel_transfer: parallelTransfer
            });
        }




        function cancelTransfer() {
            if (currentTransferId) {

                addLogWarning('🛑 用户请求取消传输...');


                const cancelBtn = document.getElementById('cancelTransferBtn');
                cancelBtn.disabled = true;
                cancelBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> 取消中...';


                socket.emit('cancel_transfer', {
                    transfer_id: currentTransferId
                });


                setTimeout(() => {
                    if (cancelBtn.disabled) {
                        addLogWarning('⚠️ 取消操作超时，强制重置界面');
                        resetTransferUI();
                        isTransferring = false;
                    }
                }, 3000);
            }
        }


        function forceCancelTransfer() {
            if (currentTransferId) {
                addLogError('🚨 强制终止传输...');


                resetTransferUI();
                isTransferring = false;


                socket.emit('cancel_transfer', {
                    transfer_id: currentTransferId,
                    force: true
                });


                currentTransferId = null;
            }
        }


        function resetTransferUI() {
            const pc = document.getElementById('progressContainer');
            if (pc) pc.style.display = 'none';
            const startBtn = document.getElementById('startTransferBtn');
            if (startBtn) startBtn.style.display = 'inline-block';


            const cancelBtn = document.getElementById('cancelTransferBtn');
            if (cancelBtn) {
                cancelBtn.style.display = 'none';
                cancelBtn.disabled = false;
                cancelBtn.innerHTML = '<i class="bi bi-stop-circle-fill"></i> 取消';
            }


            const routeEl0 = document.getElementById('transferRoute');
            if (routeEl0) routeEl0.innerHTML = '<i class="bi bi-arrow-right-circle-fill me-1"></i>准备传输...';


            { const el = document.getElementById('transferStatus'); if (el) el.textContent = '准备中'; }
            { const el = document.getElementById('transferSpeed'); if (el) el.textContent = '-'; }
            { const el = document.getElementById('transferredBytes'); if (el) el.textContent = '-'; }
            { const el = document.getElementById('eta'); if (el) el.textContent = '-'; }

            transferRefreshOverride = null;
            latestTransferredBytes = 0;
            currentTransferId = null;
            clearTransferContext();
        }


        socket.on('transfer_started', function(data) {
            isTransferring = true;
            currentTransferId = data.transfer_id;
            latestTransferredBytes = 0;
            window.transferModeLogged = false;
            { const el = document.getElementById('transferStatus'); if (el) el.textContent = '传输中...'; }
            { const el = document.getElementById('elapsedTime'); if (el) el.textContent = '00:00:00'; }
            { const el = document.getElementById('transferSpeed'); if (el) el.textContent = '0 MB/s'; }
            { const el = document.getElementById('transferredBytes'); if (el) el.textContent = '0 B'; }

        });



        socket.on('transfer_log', function(data) {
            if (data.transfer_id === currentTransferId) {
                const msg = data.message || '';
                const keep = msg.startsWith('📤 源:') || msg.startsWith('📥 目标:') || msg.startsWith('📋 文件:') || msg.startsWith('❌') || (msg.includes('✅') && (msg.includes('传输完成') || msg.includes('完成')));
                if (keep) {
                    if (msg.includes('✅')) {
                        addLogSuccess(msg);
                    } else if (msg.startsWith('❌')) {
                        addLogError(msg);
                    } else {
                        addLogInfo(msg);
                    }
                }
            }
        });


        socket.on('speed_update', function(data) {
            if (data.transfer_id === currentTransferId) {

                if (data.speed) {
                    const el = document.getElementById('transferSpeed');
                    if (el) el.textContent = data.speed;
                }


                if (data.elapsed_time) {
                    const el = document.getElementById('elapsedTime');
                    if (el) el.textContent = data.elapsed_time;
                }


                if (typeof data.transferred_human === 'string' || typeof data.transferred_bytes === 'number') {
                    if (typeof data.transferred_bytes === 'number') {
                        latestTransferredBytes = data.transferred_bytes;
                    }
                    const el = document.getElementById('transferredBytes');
                    if (el) {
                        if (typeof data.transferred_human === 'string') {
                            el.textContent = data.transferred_human;
                        } else if (typeof data.transferred_bytes === 'number') {
                            el.textContent = data.transferred_bytes === 0 ? '0 B' : formatFileSize(data.transferred_bytes);
                        }
                    }
                }


                if (data.source_server && data.target_server) {
                    const sourceDisplay = data.source_server === 'localhost' ? '本地' : data.source_server;
                    const targetDisplay = data.target_server === 'localhost' ? '本地' : data.target_server;

                    let routeIcon = '🔄';
                    if (data.transfer_mode === 'local_to_remote') {
                        routeIcon = '📤';
                    } else if (data.transfer_mode === 'remote_to_local') {
                        routeIcon = '📥';
                    }

                    const newRoute = `${routeIcon} ${sourceDisplay} → ${targetDisplay}`;
                    const routeEl = document.getElementById('transferRoute');
                    if (routeEl) {
                        const currentRoute = routeEl.innerHTML;

                        if (currentRoute !== newRoute) {
                            routeEl.innerHTML = newRoute;
                        }
                    }
                }
            }
        });

        socket.on('run_output', function(data) {
            const runId = data.run_id || 'run';
            const msg = data.message || '';
            const isError = data.is_error === true;
            const isFinal = data.final === true;
            const exitCode = typeof data.exit_code === 'number' ? data.exit_code : null;
            appendRunLog(runId, msg, isError, isFinal, exitCode);
            if (isFinal && currentRunId === runId) {
                currentRunId = null;
                updateRunControls();
            }
        });

        socket.on('transfer_complete', function(data) {
            if (data.transfer_id === currentTransferId) {

                isTransferring = false;

                const refreshOverride = transferRefreshOverride;
                transferRefreshOverride = null;

                if (data.status === 'success') {
                    { const el = document.getElementById('transferStatus'); if (el) el.textContent = '传输完成'; }
                    { const el = document.getElementById('transferRoute'); if (el) el.innerHTML = '<i class="bi bi-check-circle-fill text-success me-1"></i>传输完成'; }

                    const transferredEl = document.getElementById('transferredBytes');
                    const transferredText = transferredEl ? String(transferredEl.textContent || '').trim() : '';
                    const transferredSuffix = (transferredText && transferredText !== '-') ? transferredText : '';


                    if (data.total_time) {
                        let formattedTime = data.total_time;
                        const parts = String(data.total_time).split(':');
                        let avgSpeedSuffix = '';
                        if (parts.length === 3) {
                            const [h, m, s] = parts;
                            formattedTime = `${h}时:${m}分:${s}秒`;
                            const totalSeconds = (parseInt(h, 10) || 0) * 3600 + (parseInt(m, 10) || 0) * 60 + (parseInt(s, 10) || 0);
                            if (totalSeconds > 0 && typeof latestTransferredBytes === 'number' && latestTransferredBytes >= 1) {
                                const avgBytesPerSec = latestTransferredBytes / totalSeconds;
                                if (avgBytesPerSec >= 1) {
                                    const avgSpeedText = formatFileSize(avgBytesPerSec);
                                    if (avgSpeedText) {
                                        avgSpeedSuffix = ` 平均传输速度 ${avgSpeedText}/s`;
                                    }
                                }
                            }
                        }
                        const completeMessage = `✅ 传输已完成${transferredSuffix}${avgSpeedSuffix} - 总耗时: ${formattedTime}`;
                        addLogSuccess(completeMessage);
                        showToast(completeMessage.replace(/^✅\s*/, ''), 'success');
                        const el = document.getElementById('elapsedTime');
                        if (el) el.textContent = formattedTime;
                    } else {
                        const completeMessage = transferredSuffix ? `✅ 传输已完成${transferredSuffix}` : '✅ 传输已完成';
                        addLogSuccess(completeMessage);
                        showToast(completeMessage.replace(/^✅\s*/, ''), 'success');
                    }

                    applyTransferOptimisticUpdate();


                    const isMoveMode = (currentTransferMode === 'move');

                    let usedRefreshOverride = false;
                    if (refreshOverride && (refreshOverride.refreshSource || refreshOverride.refreshTarget)) {
                        if (refreshOverride.refreshSource) {
                            const sourceServer = document.getElementById('sourceServer').value;
                            if (sourceServer && currentSourcePath) {
                                refreshSourceAsync({ silent: true });
                            }
                        }
                        if (refreshOverride.refreshTarget) {
                            const targetServer = document.getElementById('targetServer').value;
                            if (targetServer && currentTargetPath) {
                                refreshTargetAsync({ silent: true });
                            }
                        }
                        usedRefreshOverride = true;
                    }

                    if (!usedRefreshOverride && currentTransferDirection === 'ltr') {
                        const targetServer = document.getElementById('targetServer').value;
                        if (targetServer && currentTargetPath) {
                            refreshTargetAsync({ silent: true });
                        }

                        if (isMoveMode) {
                            const sourceServer = document.getElementById('sourceServer').value;
                            if (sourceServer && currentSourcePath) {
                                refreshSourceAsync({ silent: true });
                            }
                        }
                    } else if (!usedRefreshOverride) {
                        const sourceServer = document.getElementById('sourceServer').value;
                        if (sourceServer && currentSourcePath) {
                            refreshSourceAsync({ silent: true });
                        }

                        if (isMoveMode) {
                            const targetServer = document.getElementById('targetServer').value;
                            if (targetServer && currentTargetPath) {
                                refreshTargetAsync({ silent: true });
                            }
                        }
                    }
                } else if (data.status === 'partial_success') {

                    { const el = document.getElementById('transferStatus'); if (el) el.textContent = '传输部分完成'; }
                    { const el = document.getElementById('transferRoute'); if (el) el.innerHTML = '<i class="bi bi-exclamation-triangle-fill text-warning me-1"></i>部分成功'; }
                    { const pb = document.getElementById('progressBar'); if (pb) pb.classList.add('bg-warning'); }


                    if (data.total_time) {
                        const el = document.getElementById('elapsedTime');
                        if (el) el.textContent = data.total_time;
                    }

                        if (data.total_time) {
                            addLogWarning(`🔶 传输部分完成 - 总耗时: ${data.total_time}`);
                        } else {
                            addLogWarning('🔶 传输部分完成（存在失败项目）');
                        }
                        showToast('⚠️ 传输部分完成', 'warning');
                        if (data.message) {
                            addLogWarning(`详情: ${data.message}`);
                        }



                    const isMoveMode = (currentTransferMode === 'move');

                    let usedRefreshOverride = false;
                    if (refreshOverride && (refreshOverride.refreshSource || refreshOverride.refreshTarget)) {
                        if (refreshOverride.refreshSource) {
                            const sourceServer = document.getElementById('sourceServer').value;
                            if (sourceServer && currentSourcePath) {
                                refreshSourceAsync({ silent: true });
                            }
                        }
                        if (refreshOverride.refreshTarget) {
                            const targetServer = document.getElementById('targetServer').value;
                            if (targetServer && currentTargetPath) {
                                refreshTargetAsync({ silent: true });
                            }
                        }
                        usedRefreshOverride = true;
                    }

                    if (!usedRefreshOverride && currentTransferDirection === 'ltr') {
                        const targetServer = document.getElementById('targetServer').value;
                        if (targetServer && currentTargetPath) {
                            refreshTargetAsync({ silent: true });
                        }
                        if (isMoveMode) {
                            const sourceServer = document.getElementById('sourceServer').value;
                            if (sourceServer && currentSourcePath) {
                                refreshSourceAsync({ silent: true });
                            }
                        }
                    } else if (!usedRefreshOverride) {
                        const sourceServer = document.getElementById('sourceServer').value;
                        if (sourceServer && currentSourcePath) {
                            refreshSourceAsync({ silent: true });
                        }
                        if (isMoveMode) {
                            const targetServer = document.getElementById('targetServer').value;
                            if (targetServer && currentTargetPath) {
                                refreshTargetAsync({ silent: true });
                            }
                        }
                    }
                } else {

                    { const el = document.getElementById('transferStatus'); if (el) el.textContent = '传输失败'; }
                    { const pb = document.getElementById('progressBar'); if (pb) pb.classList.add('bg-danger'); }
                    addLogError(`❌ 传输失败: ${data.message}`);
                    showToast('❌ 传输失败', 'error');
                }


                setTimeout(resetTransferUI, 100);
            }
        });


        socket.on('transfer_cancelled', function(data) {
            if (data.transfer_id === currentTransferId) {

                isTransferring = false;
                transferRefreshOverride = null;
                clearTransferContext();

                if (data.status === 'success') {
                    { const el = document.getElementById('transferStatus'); if (el) el.textContent = '传输已取消'; }
                    { const pb = document.getElementById('progressBar'); if (pb) { pb.classList.remove('progress-bar-animated'); pb.classList.add('bg-warning'); } }
                    addLogWarning('⚠️ 传输已取消');
                } else {
                    addLogError(`❌ 取消传输失败: ${data.message}`);
                }

                setTimeout(resetTransferUI, 2000);
            }
        });


        function initializeResizers() {

            const verticalResizer = document.getElementById('verticalResizer');
            const sourcePanel = document.getElementById('sourcePanel');
            const targetPanel = document.getElementById('targetPanel');

            if (sourcePanel) {
                sourcePanel.style.flexBasis = '50%';
                sourcePanel.style.width = '50%';
            }
            if (targetPanel) {
                targetPanel.style.flexBasis = '50%';
                targetPanel.style.width = '50%';
            }
            if (verticalResizer) {
                verticalResizer.style.pointerEvents = 'none';
                verticalResizer.style.cursor = 'default';
            }
        }


        function getDefaultPath(serverIP) {
            const meta = SERVERS_DATA && SERVERS_DATA[serverIP];
            if (meta && meta.default_path) return meta.default_path;
            return '';
        }

        function getRememberedPath(serverIP, isSource) {
            const panel = isSource ? 'source' : 'target';
            const rec = (REMEMBERED_PATHS && REMEMBERED_PATHS[panel]) || null;
            if (rec && rec.server === serverIP && rec.path) {
                return rec.path;
            }
            return null;
        }

        function getDefaultPathWithRemember(serverIP, isSource) {
            const rem = getRememberedPath(serverIP, isSource);
            if (rem) return rem;
            return getDefaultPath(serverIP);
        }

        function updateRememberedCache(panel, server, path) {
            if (!panel || !server || !path) return;
            if (typeof REMEMBERED_PATHS !== 'object' || REMEMBERED_PATHS === null) return;
            REMEMBERED_PATHS[panel] = { server, path };
        }

        function applyRememberedSelections() {
            if (!REMEMBERED_PATHS || Object.keys(REMEMBERED_PATHS).length === 0) return;

            const applyOne = (panelKey, selectId) => {
                const selectEl = document.getElementById(selectId);
                const rec = REMEMBERED_PATHS && REMEMBERED_PATHS[panelKey];
                if (!selectEl || !rec || !rec.server || !rec.path) return false;
                const hasOption = Array.from(selectEl.options || []).some(opt => opt.value === rec.server);
                if (!hasOption) return false;
                selectEl.value = rec.server;

                selectEl.dispatchEvent(new Event('change'));
                return true;
            };

            const sourceApplied = applyOne('source', 'sourceServer');
            const targetApplied = applyOne('target', 'targetServer');
            if (sourceApplied || targetApplied) {
                addLogInfo('🧭 已自动恢复上次使用的服务器和路径');
            }
        }


        function isWindowsServer(serverIP) {
            try {
                return SERVERS_DATA[serverIP] && SERVERS_DATA[serverIP].os_type === 'windows';
            } catch (error) {
                console.error('检查服务器类型失败:', error);
                return false;
            }
        }


        async function loadWindowsDrives(serverIP, isSource) {
            try {
                const response = await fetch(`/api/windows_drives/${serverIP}`);
                const data = await response.json();

                const normalizeDrive = (letter) => {
                    if (!letter) return null;
                    const upper = String(letter).toUpperCase();
                    return upper.endsWith(':') ? upper : `${upper}:`;
                };

                if (data.success && data.drives) {
                    if (isSource) {
                        windowsDrivesSource = data.drives;
                    } else {
                        windowsDrivesTarget = data.drives;
                    }

                    const desiredPath = isSource ? currentSourcePath : currentTargetPath;
                    const desiredDrive = normalizeDrive((desiredPath || '').split(/[\\/]/)[0]);

                    const preferred = data.drives.find(d => normalizeDrive(d.letter) === desiredDrive) ||
                        data.drives.find(d => normalizeDrive(d.letter) === 'C:') ||
                        data.drives[0];

                    if (preferred) {
                        const driveLabel = normalizeDrive(preferred.letter);
                        const driveRoot = driveLabel ? `${driveLabel}/` : '/';
                        const useDesiredPath = Boolean(desiredDrive && driveLabel === desiredDrive && desiredPath);
                        const targetPath = useDesiredPath ? desiredPath : driveRoot;

                        if (isSource) {
                            if (currentSourcePath !== targetPath) {
                                currentSourcePath = targetPath;
                                browseSourceInstant(currentSourcePath);
                            }
                            updatePathNavigation(currentSourcePath, true);
                        } else {
                            if (currentTargetPath !== targetPath) {
                                currentTargetPath = targetPath;
                                browseTargetInstant(currentTargetPath);
                            }
                            updatePathNavigation(currentTargetPath, false);
                        }
                        if (!useDesiredPath) {
                            addLogInfo(`💾 默认选择磁盘: ${preferred.letter}`);
                        }
                    }

                    addLogInfo(`💾 已加载Windows磁盘列表: ${data.drives.length}个磁盘`);
                } else {
                    console.error('加载磁盘列表失败:', data.error);
                }
            } catch (error) {
                console.error('加载Windows磁盘列表异常:', error);
            }
        }


        function hideWindowsDriveSelector(isSource) {
            const el = document.getElementById(isSource ? 'sourceDriveSelector' : 'targetDriveSelector');
            if (el) el.style.display = 'none';
        }


        function switchLogoDisplay(mode) {

            console.log(`LOGO显示模式: ${mode === 'header' ? '标题并排（当前）' : mode}`);
        }




            const PreviewCache = new Map(); // key -> { type: 'blob'|'text', value: string, ts: number }

            function normalizePreviewPath(path) {
                return String(path || '').replace(/\\/g, '/');
            }

            function getPreviewKey(server, path, variant = '') {
                const base = `${server}|${normalizePreviewPath(path)}`;
                return variant ? `${base}|${variant}` : base;
            }

            function revokePreviewEntry(entry) {
                if (!entry) return;
                if (entry.type === 'blob' && typeof entry.value === 'string' && entry.value.startsWith('blob:')) {
                    try { URL.revokeObjectURL(entry.value); } catch (_) {}
                }
            }

            function previewCacheGet(server, path, type, variant = '') {
                const key = getPreviewKey(server, path, variant);
                const entry = PreviewCache.get(key);
                if (!entry) return null;
                if (type && entry.type !== type) return null;
                return entry;
            }

            function previewCacheSet(server, path, type, value, variant = '') {
                const key = getPreviewKey(server, path, variant);
                const prev = PreviewCache.get(key);
                if (prev && prev.type === 'blob' && prev.value !== value) {
                    revokePreviewEntry(prev);
                }
                PreviewCache.set(key, { type, value, ts: Date.now() });
            }

            function invalidatePreviewCache(server, paths) {
                if (!server || !Array.isArray(paths) || paths.length === 0) return;
                const normalized = paths.map(normalizePreviewPath).filter(Boolean);
                if (normalized.length === 0) return;

                const serverPrefix = `${server}|`;
                const exact = new Set(normalized);
                const prefixes = normalized.map(p => (p.endsWith('/') ? p : p + '/'));

                const keysToDelete = [];
                for (const key of PreviewCache.keys()) {
                    if (!key.startsWith(serverPrefix)) continue;
                    const cachedPath = key.slice(serverPrefix.length).split('|')[0];
                    if (exact.has(cachedPath) || prefixes.some(prefix => cachedPath.startsWith(prefix))) {
                        keysToDelete.push(key);
                    }
                }
                keysToDelete.forEach((k) => {
                    const entry = PreviewCache.get(k);
                    revokePreviewEntry(entry);
                    PreviewCache.delete(k);
                });
            }

            function invalidatePreviewCacheUnderDir(server, dirPath) {
                if (!server || !dirPath) return;
                invalidatePreviewCache(server, [dirPath]);
            }
            const ImageViewer = {
                items: [],
                index: -1,
                server: '',
                isSource: true
            };
            let imageDeleteInFlight = false;

        const IMAGE_DPR_CAP = 2;
        const IMAGE_PREVIEW_MAX_DIM = 2200;
        const IMAGE_PREVIEW_MIN_DIM = 320;
        const IMAGE_PREVIEW_QUALITY = 82;
        const IMAGE_GRID_THUMB_MAX = 1024;
        const IMAGE_GRID_THUMB_MIN = 120;
        const IMAGE_GRID_THUMB_QUALITY = 82;

        function clampNumber(value, min, max) {
            if (!Number.isFinite(value)) return min;
            if (value < min) return min;
            if (value > max) return max;
            return value;
        }

        function getImagePreviewRequestSize() {
            const dpr = Math.min(IMAGE_DPR_CAP, window.devicePixelRatio || 1);
            const width = clampNumber(Math.round(window.innerWidth * 0.98 * dpr), IMAGE_PREVIEW_MIN_DIM, IMAGE_PREVIEW_MAX_DIM);
            const height = clampNumber(Math.round(window.innerHeight * 0.95 * dpr), IMAGE_PREVIEW_MIN_DIM, IMAGE_PREVIEW_MAX_DIM);
            return { width, height, quality: IMAGE_PREVIEW_QUALITY };
        }

        async function getImageBlobUrl(server, path, options = {}) {
            const width = Math.max(0, Number.parseInt(options.width, 10) || 0);
            const height = Math.max(0, Number.parseInt(options.height, 10) || 0);
            const quality = Math.max(0, Number.parseInt(options.quality, 10) || 0);
            const interp = options.interp ? String(options.interp) : '';
            const format = options.format ? String(options.format).toLowerCase() : '';
            const interpKey = interp ? `i${interp}` : '';
            const formatKey = format ? `f${format}` : '';
            const variant = (width || height || quality || interpKey || formatKey)
                ? `${width}x${height}q${quality}${interpKey ? '-' + interpKey : ''}${formatKey ? '-' + formatKey : ''}`
                : '';
            const cached = previewCacheGet(server, path, 'blob', variant);
            if (cached) return cached.value;

            const params = new URLSearchParams();
            params.set('server', server);
            params.set('path', path);
            if (width > 0) params.set('width', String(width));
            if (height > 0) params.set('height', String(height));
            if (quality > 0) params.set('quality', String(quality));
            if (interp) params.set('interp', interp);
            if (format) params.set('format', format);
            const resp = await fetch(`/api/image/stream?${params.toString()}`, { cache: 'no-store' });
            if (!resp.ok) {
                let msg = '';
                try { msg = await resp.text(); } catch (_) {}
                throw new Error(`HTTP ${resp.status}${msg ? ': ' + msg : ''}`);
            }
            const blob = await resp.blob();
            const blobUrl = URL.createObjectURL(blob);
            previewCacheSet(server, path, 'blob', blobUrl, variant);
            return blobUrl;
        }

        function isImageFile(name) {
            return /\.(jpg|jpeg|png|gif|bmp|webp|svg)$/i.test(name);
        }
        function isTextEditable(name) {
            return /\.(txt|xml|py|js|css|html?|json|md|log|conf|ini|yml|yaml|sh|c|cpp|h|hpp)$/i.test(name);
        }
        function isArchiveFile(name) {
            return /\.(zip|tar|tar\.gz|tgz|tar\.bz2|tar\.xz)$/i.test(name);
        }

            function buildImageViewer(server, path, name, isSource) {
                const state = isSource ? browseState.source : browseState.target;
                const allItems = state.fullItems && state.fullItems.length ? state.fullItems : [];
                const imgs = [];
                let currentIndex = -1;
                allItems.forEach((it, idx) => {
                    if (!it || it.is_directory) return;
                    if (!isImageFile(it.name)) return;
                    if (it.name === name && it.path === path) {
                        currentIndex = imgs.length;
                    }
                    imgs.push({ path: it.path, name: it.name });
                });
                ImageViewer.items = imgs;
                ImageViewer.index = currentIndex >= 0 ? currentIndex : 0;
                ImageViewer.server = server;
                ImageViewer.isSource = isSource;
            }

            function applyImageTransform(img) {
                if (!img) return;
                img.style.transform = `translate(${imageOffsetX}px, ${imageOffsetY}px) scale(${imageZoom})`;
            }

            function _getImageSizeText(img) {
                if (!img) return '';
                const w = img.naturalWidth || 0;
                const h = img.naturalHeight || 0;
                if (!w || !h) return '';
                return `${w}x${h}`;
            }

            function _updateImageCaption(item, index, len, sizeText) {
                const caption = document.getElementById('imagePreviewCaption');
                if (!caption || !item) return;
                const base = `${item.name} (${index + 1}/${len})`;
                caption.textContent = sizeText ? `${base} | ${sizeText}` : base;
            }

            async function showImageAt(index) {
                if (!ImageViewer.items || ImageViewer.items.length === 0) return;
                const len = ImageViewer.items.length;
                if (len === 0) return;
                if (index < 0) index = len - 1;
                if (index >= len) index = 0;
                ImageViewer.index = index;
                const item = ImageViewer.items[index];
                const server = ImageViewer.server;


                const modal = document.getElementById('imagePreviewModal');
                const img = modal.querySelector('img');
                if (modal && img) {
                    modal.style.display = 'block';
                    img.style.opacity = '0.3';
                    img.decoding = 'async';
                    img.dataset.imagePath = item.path || '';
                    img.src = 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgZmlsbD0iI2Y1ZjVmNSIvPjx0ZXh0IHg9IjUwIiB5PSI1NSIgZm9udC1mYW1pbHk9IkFyaWFsIiBmb250LXNpemU9IjE0IiBmaWxsPSIjOTk5IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj5Loading...</dGV4dD48L3N2Zz4=';
                    imageZoom = 1;
                    imageOffsetX = 0;
                    imageOffsetY = 0;
                    applyImageTransform(img);
                }

                try {
                    const blobUrl = await getImageBlobUrl(server, item.path);


                    if (img) {
                        const expectedPath = item.path || '';
                        img.onload = () => {
                            if (img.dataset.imagePath !== expectedPath) return;
                            img.style.opacity = '1';
                            _updateImageCaption(item, index, len, _getImageSizeText(img));
                        };
                        img.src = blobUrl;
                    }
                    _updateImageCaption(item, index, len, '');
                } catch (e) {
                    addLogError('图片预览失败: ' + (e.message || e));
                    console.error('Image preview fetch error:', e);
                    closeImageModal();
                }
            }

            async function previewImage(server, path, name, isSource) {
                try {
                    const state = isSource ? browseState.source : browseState.target;
                    if (!state.fullItems || state.fullHasMore) {
                        await ensureAllItemsLoaded(isSource);
                    }
                    buildImageViewer(server, path, name, isSource);
                    await showImageAt(ImageViewer.index);
                } catch (e) {
                    addLogError('图片预览失败: ' + (e.message || e));
                    console.error('Image preview fetch error:', e);
                    closeImageModal();
                }
            }

            async function deleteCurrentPreviewImage() {
                if (!ImageViewer.items || ImageViewer.items.length === 0) return false;
                if (ImageViewer.index < 0) return false;
                const item = ImageViewer.items[ImageViewer.index];
                if (!item || !item.path) return false;
                const ok = await deletePathsDirect(ImageViewer.server, [item.path], ImageViewer.isSource);
                if (!ok) return false;
                removeImageGridItem(item.path);
                ImageViewer.items.splice(ImageViewer.index, 1);
                if (!ImageViewer.items.length) {
                    closeImageModal();
                    return true;
                }
                if (ImageViewer.index >= ImageViewer.items.length) {
                    ImageViewer.index = ImageViewer.items.length - 1;
                }
                await showImageAt(ImageViewer.index);
                return true;
            }

            async function editTextFile(server, path, name) {
                try {

                    const cached = previewCacheGet(server, path, 'text');
                    openEditorModal(server, path, name, cached ? cached.value : '正在加载...');
                    if (cached) return;


                    const url = `/api/file/read?server=${encodeURIComponent(server)}&path=${encodeURIComponent(path)}`;
                    const resp = await fetch(url, { cache: 'no-store' });
                    if (!resp.ok) {
                        let msg = '';
                        try { msg = await resp.text(); } catch(_) {}
                        throw new Error(`HTTP ${resp.status}${msg ? ': ' + msg : ''}`);
                    }
                    const data = await resp.json();
                    if (!data.success) throw new Error(data.error || '读取失败');
                    const content = data.content || '';
                    previewCacheSet(server, path, 'text', content);


                    const ta = document.querySelector('#editorModal textarea');
                    if (ta) {
                        ta.value = content;
                        updateEditorLineNumbers();
                        renderFindHighlights();
                        syncEditorScroll();
                        startEditorScrollSync();
                    }
                } catch (e) {
                    addLogError('打开编辑器失败: ' + (e.message || e));

                    closeEditorModal();
                }
            }

            function handleFileItemDblClick(el, isSource) {
                try {
                    const path = el.dataset.path;
                    const name = el.dataset.name || '';
                    const isDir = String(el.dataset.isDirectory).toLowerCase() === 'true';
                    if (isDir) {

                        return;
                    }
                    const server = (isSource ? document.getElementById('sourceServer') : document.getElementById('targetServer')).value;
                    if (!server) return;
                    if (isImageFile(name)) {
                        previewImage(server, path, name, isSource);
                    } else if (isTextEditable(name)) {
                        editTextFile(server, path, name);
                    }
                } catch (e) {
                    console.error('handleFileItemDblClick error:', e);
                }
            }

            function openImageModal(src, title) {
                const modal = document.getElementById('imagePreviewModal');
                if (!modal) return;
                const img = modal.querySelector('img');
                if (img) img.src = src;
                modal.style.display = 'block';
            }
            function closeImageModal() {
                const modal = document.getElementById('imagePreviewModal');
                if (!modal) return;
                modal.style.display = 'none';
                const img = modal.querySelector('img');
                if (img) img.removeAttribute('src');
                imageZoom = 1;
                imageOffsetX = 0;
                imageOffsetY = 0;
                const caption = document.getElementById('imagePreviewCaption');
                if (caption) caption.textContent = '';
                ImageViewer.items = [];
                ImageViewer.index = -1;
	            }

		            let imageGridObserver = null;
		            const IMAGE_GRID_COLS_DEFAULT = 8;
		            const IMAGE_GRID_COLS_OPTIONS = [4, 6, 8, 10, 12];
		            let imageGridCols = IMAGE_GRID_COLS_DEFAULT;
		            let imageGridColsControlsBound = false;
		            let imageGridColsRaf = 0;
            const IMAGE_GRID_MAX_PARALLEL = 16;
            const IMAGE_GRID_MAX_PARALLEL_EAGER = 24;
            const IMAGE_GRID_MAX_PARALLEL_BURST = 28;
            const IMAGE_GRID_BURST_DURATION = 1200;
            const IMAGE_GRID_EAGER_SCROLL_RATIO = 0.12;
            const imageGridLoadQueue = [];
            let imageGridLoadingCount = 0;
            let imageGridThumbWidth = 0;
            let imageGridResizeRaf = 0;
            let imageGridColsApplyRaf = 0;
            let imageGridResumeTimer = 0;
            let imageGridPendingCols = null;
            let imageGridSwitching = false;
            let imageGridEagerMode = false;
            let imageGridEagerQueued = false;
            let imageGridScrollBound = false;
            const IMAGE_GRID_VIRTUAL_THRESHOLD = 320;
            const IMAGE_GRID_VIRTUAL_OVERSCAN = 6;
            let imageGridItems = [];
            let imageGridVirtualEnabled = false;
            let imageGridRowHeight = 0;
            let imageGridRowGap = 0;
            let imageGridRowMeasured = false;
            let imageGridRenderRaf = 0;
            let imageGridScrollRaf = 0;
            let imageGridVirtualForce = false;
            let imageGridLastRender = { startIndex: -1, endIndex: -1, cols: 0, total: 0 };
            let imageGridActiveServer = '';
            let imageGridActiveIsSource = true;
            let imageGridSelectedPath = '';
            let imageGridSelectedName = '';
            let imageGridBurstUntil = 0;
            let imageGridLastScrollTop = 0;

		            function computeImageGridThumbWidth(colsOverride) {
		                const container = document.getElementById('imageGridContainer');
		                if (!container) return IMAGE_GRID_THUMB_MIN;
		                const cols = colsOverride || imageGridCols || IMAGE_GRID_COLS_DEFAULT;
		                const style = getComputedStyle(container);
		                const paddingLeft = parseFloat(style.paddingLeft) || 0;
		                const paddingRight = parseFloat(style.paddingRight) || 0;
		                const gap = parseFloat(style.columnGap || style.gap || 0) || 0;
		                const available = container.clientWidth - paddingLeft - paddingRight - gap * Math.max(cols - 1, 0);
		                if (!Number.isFinite(available) || available <= 0) return IMAGE_GRID_THUMB_MIN;
		                const colWidth = available / cols;
		                const dpr = Math.min(IMAGE_DPR_CAP, window.devicePixelRatio || 1);
		                const scaled = Math.round(colWidth * dpr);
		                return clampNumber(scaled, IMAGE_GRID_THUMB_MIN, IMAGE_GRID_THUMB_MAX);
		            }

            function scheduleImageGridThumbUpdate() {
                if (imageGridResizeRaf) return;
                imageGridResizeRaf = requestAnimationFrame(() => {
                    imageGridThumbWidth = computeImageGridThumbWidth();
                    imageGridResizeRaf = 0;
                });
            }

		            function getImageGridParallelLimit() {
		                if (imageGridBurstUntil && Date.now() < imageGridBurstUntil) {
		                    return IMAGE_GRID_MAX_PARALLEL_BURST;
		                }
		                return imageGridEagerMode ? IMAGE_GRID_MAX_PARALLEL_EAGER : IMAGE_GRID_MAX_PARALLEL;
		            }

		            function normalizeImageGridCols(value) {
		                const n = Number.parseInt(value, 10);
		                if (!Number.isFinite(n)) return IMAGE_GRID_COLS_DEFAULT;
		                return IMAGE_GRID_COLS_OPTIONS.includes(n) ? n : IMAGE_GRID_COLS_DEFAULT;
	            }

            function observePendingImageGridCards() {
                if (imageGridVirtualEnabled) return;
                if (!imageGridObserver) return;
                const container = document.getElementById('imageGridContainer');
                if (!container) return;
                container.querySelectorAll('.image-grid-card').forEach(card => {
                    const imgEl = card.querySelector('img');
                    if (!imgEl || imgEl.dataset.loaded === '1' || imgEl.dataset.loading === '1') return;
                    imageGridObserver.observe(card);
                });
            }

            function shouldUseImageGridVirtual(count) {
                return count >= IMAGE_GRID_VIRTUAL_THRESHOLD;
            }

            function setImageGridVirtualEnabled(enabled) {
                const next = Boolean(enabled);
                if (next === imageGridVirtualEnabled) return false;
                imageGridVirtualEnabled = next;
                imageGridRowHeight = 0;
                imageGridRowGap = 0;
                imageGridRowMeasured = false;
                imageGridLastRender = { startIndex: -1, endIndex: -1, cols: 0, total: 0 };
                if (imageGridObserver) {
                    try { imageGridObserver.disconnect(); } catch (_) {}
                    imageGridObserver = null;
                }
                return true;
            }

            function scheduleImageGridVirtualRender(force = false) {
                if (!imageGridVirtualEnabled) return;
                imageGridVirtualForce = imageGridVirtualForce || force;
                if (imageGridRenderRaf) return;
                imageGridRenderRaf = requestAnimationFrame(() => {
                    const forceNow = imageGridVirtualForce;
                    imageGridVirtualForce = false;
                    imageGridRenderRaf = 0;
                    renderImageGridVirtual(forceNow);
                });
            }

            function computeImageGridRowMetrics() {
                const container = document.getElementById('imageGridContainer');
                if (!container) return;
                const style = getComputedStyle(container);
                imageGridRowGap = parseFloat(style.rowGap || style.gap || 0) || 0;
                const sample = container.querySelector('.image-grid-card');
                if (sample) {
                    const rect = sample.getBoundingClientRect();
                    if (rect && rect.height) {
                        imageGridRowHeight = rect.height;
                    }
                    return;
                }
                const cols = imageGridCols || IMAGE_GRID_COLS_DEFAULT;
                const paddingLeft = parseFloat(style.paddingLeft) || 0;
                const paddingRight = parseFloat(style.paddingRight) || 0;
                const gap = imageGridRowGap || 0;
                const available = container.clientWidth - paddingLeft - paddingRight - gap * Math.max(cols - 1, 0);
                if (available <= 0) return;
                const colWidth = available / cols;
                const imgHeight = colWidth * 150 / 220;
                imageGridRowHeight = Math.max(1, Math.round(imgHeight + 48));
            }

            function _escapeCssSelector(value) {
                if (window.CSS && typeof window.CSS.escape === 'function') {
                    return window.CSS.escape(value);
                }
                return String(value).replace(/["\\]/g, '\\$&');
            }

            function setImageGridSelection(path, name) {
                imageGridSelectedPath = path || '';
                imageGridSelectedName = name || '';
                const container = document.getElementById('imageGridContainer');
                if (!container) return;
                container.querySelectorAll('.image-grid-card.selected').forEach(el => el.classList.remove('selected'));
                if (!imageGridSelectedPath) return;
                const selector = `.image-grid-card[data-path="${_escapeCssSelector(imageGridSelectedPath)}"]`;
                const card = container.querySelector(selector);
                if (card) card.classList.add('selected');
            }

            function clearImageGridSelection() {
                setImageGridSelection('', '');
            }

            function removeImageGridItem(path) {
                if (!path) return;
                const idx = imageGridItems.findIndex(it => it && it.path === path);
                if (idx < 0) return;
                imageGridItems.splice(idx, 1);
                for (let i = imageGridLoadQueue.length - 1; i >= 0; i--) {
                    if (imageGridLoadQueue[i] && imageGridLoadQueue[i].path === path) {
                        imageGridLoadQueue.splice(i, 1);
                    }
                }
                if (imageGridSelectedPath === path) {
                    clearImageGridSelection();
                }
                const container = document.getElementById('imageGridContainer');
                if (!container) return;
                if (imageGridVirtualEnabled) {
                    imageGridLastRender = { startIndex: -1, endIndex: -1, cols: 0, total: 0 };
                    scheduleImageGridVirtualRender(true);
                    return;
                }
                const selector = `.image-grid-card[data-path="${_escapeCssSelector(path)}"]`;
                const card = container.querySelector(selector);
                if (card) card.remove();
                if (!imageGridItems.length) {
                    container.innerHTML = '<div class="image-grid-empty">当前目录没有图片</div>';
                }
            }

            function createImageGridCard(item, server, isSource) {
                const card = document.createElement('div');
                card.className = 'image-grid-card';
                card.dataset.server = server;
                card.dataset.path = item.path;
                card.dataset.name = item.name;
                if (imageGridSelectedPath && imageGridSelectedPath === item.path) {
                    card.classList.add('selected');
                }

                const imgEl = document.createElement('img');
                imgEl.alt = item.name;
                imgEl.loading = 'eager';
                imgEl.decoding = 'async';
                imgEl.dataset.loaded = '0';

                const nameEl = document.createElement('div');
                nameEl.className = 'image-grid-name';
                nameEl.textContent = item.name;

                card.appendChild(imgEl);
                card.appendChild(nameEl);
                card.addEventListener('click', () => {
                    setImageGridSelection(item.path, item.name);
                });
                card.addEventListener('dblclick', () => {
                    previewImage(server, item.path, item.name, isSource);
                });

                if (!imageGridVirtualEnabled && !imageGridEagerMode && imageGridObserver) {
                    imageGridObserver.observe(card);
                } else {
                    loadImageCardImmediately(card);
                }

                return card;
            }

            function renderImageGridVirtual(force = false) {
                if (!imageGridVirtualEnabled) return;
                const container = document.getElementById('imageGridContainer');
                const modal = document.getElementById('imageGridModal');
                if (!container || !modal) return;

                if (!imageGridItems || imageGridItems.length === 0) {
                    container.innerHTML = '<div class="image-grid-empty">当前目录没有图片</div>';
                    return;
                }

                if (!imageGridRowHeight) {
                    computeImageGridRowMetrics();
                }

                const cols = imageGridCols || IMAGE_GRID_COLS_DEFAULT;
                const rowGap = imageGridRowGap || 0;
                const rowHeight = imageGridRowHeight || 220;
                const rowStep = rowHeight + rowGap;
                const totalRows = Math.ceil(imageGridItems.length / cols);
                const scrollTop = modal.scrollTop || 0;
                const containerTop = container.offsetTop || 0;
                const viewportTop = Math.max(0, scrollTop - containerTop);
                const viewportHeight = modal.clientHeight || 0;
                const startRow = Math.max(0, Math.floor(viewportTop / rowStep) - IMAGE_GRID_VIRTUAL_OVERSCAN);
                const endRow = Math.min(totalRows, Math.ceil((viewportTop + viewportHeight) / rowStep) + IMAGE_GRID_VIRTUAL_OVERSCAN);
                const startIndex = startRow * cols;
                const endIndex = Math.min(imageGridItems.length, endRow * cols);

                if (!force &&
                    imageGridLastRender.startIndex === startIndex &&
                    imageGridLastRender.endIndex === endIndex &&
                    imageGridLastRender.cols === cols &&
                    imageGridLastRender.total === imageGridItems.length) {
                    return;
                }

                imageGridLastRender = { startIndex, endIndex, cols, total: imageGridItems.length };
                if (force) {
                    imageGridLoadQueue.length = 0;
                    imageGridLoadingCount = 0;
                }

                const frag = document.createDocumentFragment();
                const topSpacer = document.createElement('div');
                topSpacer.className = 'image-grid-spacer';
                topSpacer.style.height = `${startRow * rowStep}px`;
                frag.appendChild(topSpacer);

                const slice = imageGridItems.slice(startIndex, endIndex);
                slice.forEach((item) => {
                    frag.appendChild(createImageGridCard(item, imageGridActiveServer, imageGridActiveIsSource));
                });

                const visibleRows = Math.max(0, endRow - startRow);
                const totalHeight = totalRows > 0 ? (totalRows * rowStep - rowGap) : 0;
                const visibleHeight = visibleRows > 0 ? (visibleRows * rowStep - rowGap) : 0;
                const topHeight = startRow * rowStep;
                const bottomHeight = Math.max(0, totalHeight - topHeight - visibleHeight);
                const bottomSpacer = document.createElement('div');
                bottomSpacer.className = 'image-grid-spacer';
                bottomSpacer.style.height = `${bottomHeight}px`;
                frag.appendChild(bottomSpacer);

                container.innerHTML = '';
                container.appendChild(frag);

                if (!imageGridRowMeasured) {
                    imageGridRowMeasured = true;
                    requestAnimationFrame(() => {
                        const sample = container.querySelector('.image-grid-card');
                        if (!sample) return;
                        const rect = sample.getBoundingClientRect();
                        if (rect && rect.height) {
                            if (Math.abs(rect.height - imageGridRowHeight) > 2) {
                                imageGridRowHeight = rect.height;
                                scheduleImageGridVirtualRender(true);
                            }
                        }
                    });
                }
            }

            function pauseImageGridLoading() {
                imageGridSwitching = true;
                if (imageGridObserver) {
                    try { imageGridObserver.disconnect(); } catch (_) {}
                }
            }

            function resumeImageGridLoading(modal) {
                imageGridSwitching = false;
                if (modal && modal.style.display !== 'none') {
                    initImageGridObserver(modal);
                    observePendingImageGridCards();
                }
                processImageGridQueue();
            }

            function applyImageGridColumns(cols) {
                const normalized = normalizeImageGridCols(cols);
                if (normalized === imageGridCols && !imageGridSwitching) return;
                imageGridPendingCols = normalized;

                if (imageGridColsApplyRaf) {
                    try { cancelAnimationFrame(imageGridColsApplyRaf); } catch (_) {}
                }
                imageGridColsApplyRaf = requestAnimationFrame(() => {
                    const nextCols = imageGridPendingCols;
                    imageGridPendingCols = null;
                    if (!nextCols) return;
                    imageGridCols = nextCols;

                    const btnWrap = document.getElementById('imageGridColsButtons');
                    if (btnWrap) {
                        btnWrap.querySelectorAll('.image-grid-cols-btn').forEach(btn => {
                            const btnCols = normalizeImageGridCols(btn.dataset.cols);
                            btn.classList.toggle('active', btnCols === nextCols);
                        });
                    }

                    const container = document.getElementById('imageGridContainer');
                    const modal = document.getElementById('imageGridModal');
                    if (!container) return;
                    if (imageGridColsRaf) {
                        try { cancelAnimationFrame(imageGridColsRaf); } catch (_) {}
                    }
                    pauseImageGridLoading();
                    container.classList.add('image-grid-relayout');
                    imageGridColsRaf = requestAnimationFrame(() => {
                        const nextThumb = computeImageGridThumbWidth(nextCols);
                        container.style.setProperty('--image-grid-cols', String(nextCols));
                        imageGridThumbWidth = nextThumb;
                        if (imageGridVirtualEnabled) {
                            imageGridRowHeight = 0;
                            imageGridRowMeasured = false;
                            scheduleImageGridVirtualRender(true);
                        }
                        imageGridColsRaf = 0;
                        if (imageGridResumeTimer) {
                            clearTimeout(imageGridResumeTimer);
                        }
                        imageGridResumeTimer = setTimeout(() => {
                            container.classList.remove('image-grid-relayout');
                            resumeImageGridLoading(modal);
                        }, 80);
                    });
                });
            }

	            function ensureImageGridColsControls() {
	                if (imageGridColsControlsBound) return;
	                const btnWrap = document.getElementById('imageGridColsButtons');
	                if (!btnWrap) return;

	                btnWrap.addEventListener('click', (e) => {
	                    const btn = e.target.closest('.image-grid-cols-btn[data-cols]');
	                    if (!btn) return;
	                    applyImageGridColumns(btn.dataset.cols);
	                });
	                imageGridColsControlsBound = true;

	                const initialBtn = btnWrap.querySelector('.image-grid-cols-btn.active[data-cols]');
		                const initialCols = initialBtn ? initialBtn.dataset.cols : IMAGE_GRID_COLS_DEFAULT;
		                applyImageGridColumns(initialCols);
		            }

            function processImageGridQueue() {
                if (imageGridSwitching) return;
                if (imageGridLoadingCount >= getImageGridParallelLimit()) return;
                const task = imageGridLoadQueue.shift();
                if (!task) return;
		                imageGridLoadingCount++;
		                const thumbWidth = task.width || imageGridThumbWidth || computeImageGridThumbWidth();
                getImageBlobUrl(task.server, task.path, { width: thumbWidth, quality: IMAGE_GRID_THUMB_QUALITY, interp: 'lanczos', format: 'webp' })
		                    .then(url => {
		                        if (task.imgEl && task.imgEl.dataset.loaded !== '1') {
		                            task.imgEl.src = url;
		                            task.imgEl.dataset.loaded = '1';
		                        }
		                    })
		                    .catch(err => {
		                        if (task.imgEl) {
		                            task.imgEl.alt = '加载失败';
		                            task.imgEl.title = err.message || String(err);
		                            task.imgEl.dataset.loaded = 'err';
		                        }
		                    })
		                    .finally(() => {
		                        imageGridLoadingCount = Math.max(0, imageGridLoadingCount - 1);
		                        requestAnimationFrame(processImageGridQueue);
		                    });
		            }

            function scheduleImageGridLoad(card, priority = false) {
                if (imageGridSwitching) return;
                const imgEl = card.querySelector('img');
                if (!imgEl || imgEl.dataset.loaded === '1' || imgEl.dataset.loading === '1') return;
		                imgEl.dataset.loading = '1';
		                const thumbWidth = imageGridThumbWidth || computeImageGridThumbWidth();
		                const task = {
		                    server: card.dataset.server,
		                    path: card.dataset.path,
		                    imgEl,
		                    width: thumbWidth
		                };
		                if (priority) {
		                    imageGridLoadQueue.unshift(task);
		                } else {
		                    imageGridLoadQueue.push(task);
		                }
		                processImageGridQueue();
		            }

            function activateImageGridBurstLoad() {
                imageGridBurstUntil = Date.now() + IMAGE_GRID_BURST_DURATION;
                const container = document.getElementById('imageGridContainer');
                if (!container) return;
                const cards = Array.from(container.querySelectorAll('.image-grid-card'));
                if (!cards.length) return;
                for (let i = imageGridLoadQueue.length - 1; i >= 0; i--) {
                    const task = imageGridLoadQueue[i];
                    if (!task || !task.imgEl || task.imgEl.dataset.loaded === '1' || !task.imgEl.isConnected) {
                        imageGridLoadQueue.splice(i, 1);
                    }
                }
                cards.forEach(card => scheduleImageGridLoad(card, true));
                for (let i = 0; i < getImageGridParallelLimit(); i++) {
                    processImageGridQueue();
                }
            }

		            function queueAllImageGridLoads(batchSize = 160) {
		                const container = document.getElementById('imageGridContainer');
		                if (!container) return;
		                const cards = Array.from(container.querySelectorAll('.image-grid-card'));
		                if (!cards.length) return;
		                let idx = 0;
		                const step = () => {
		                    const end = Math.min(cards.length, idx + batchSize);
		                    for (; idx < end; idx++) {
		                        scheduleImageGridLoad(cards[idx]);
		                    }
		                    if (idx < cards.length) {
		                        requestAnimationFrame(step);
		                    }
		                };
		                step();
		            }

		            function activateImageGridEagerLoad() {
		                if (imageGridEagerMode) return;
		                imageGridEagerMode = true;
		                if (imageGridObserver) imageGridObserver.disconnect();
		                if (!imageGridEagerQueued) {
		                    imageGridEagerQueued = true;
		                    queueAllImageGridLoads();
		                    for (let i = 0; i < getImageGridParallelLimit(); i++) {
		                        processImageGridQueue();
		                    }
		                }
		            }

	            function initImageGridObserver(rootEl) {
	                if (imageGridVirtualEnabled) {
	                    imageGridObserver = null;
	                    return;
	                }
	                if (imageGridObserver) imageGridObserver.disconnect();
	                if (!('IntersectionObserver' in window)) {
	                    imageGridObserver = null;
                    return;
                }
	                imageGridObserver = new IntersectionObserver((entries) => {
	                    entries.forEach(entry => {
	                        if (!entry.isIntersecting) return;
	                        const card = entry.target;
	                        scheduleImageGridLoad(card);
	                        imageGridObserver && imageGridObserver.unobserve(card);
	                    });
	                }, {
	                    root: rootEl || null,
	                    rootMargin: '600px',
	                    threshold: 0.01
	                });
	            }

	            function loadImageCardImmediately(card) {
	                scheduleImageGridLoad(card);
	            }

	            function ensureImageGridScrollWatcher(modal) {
	                if (imageGridScrollBound || !modal) return;
	                modal.addEventListener('scroll', () => {
	                    if (modal.style.display === 'none') return;
	                    if (imageGridVirtualEnabled) {
	                        const currentTop = modal.scrollTop || 0;
	                        imageGridLastScrollTop = currentTop;
	                        if (!imageGridScrollRaf) {
	                            imageGridScrollRaf = requestAnimationFrame(() => {
	                                imageGridScrollRaf = 0;
	                                scheduleImageGridVirtualRender();
	                                activateImageGridBurstLoad();
	                            });
	                        }
	                        return;
	                    }
	                    if (imageGridEagerMode) return;
	                    const maxScroll = modal.scrollHeight - modal.clientHeight;
	                    if (maxScroll <= 0) return;
	                    const ratio = modal.scrollTop / maxScroll;
	                    if (ratio >= IMAGE_GRID_EAGER_SCROLL_RATIO) {
	                        activateImageGridEagerLoad();
	                    }
	                }, { passive: true });
	                imageGridScrollBound = true;
	            }

            window.addEventListener('resize', () => {
                const modal = document.getElementById('imageGridModal');
                if (!modal || modal.style.display === 'none') return;
                scheduleImageGridThumbUpdate();
                if (imageGridVirtualEnabled) {
                    imageGridRowHeight = 0;
                    imageGridRowMeasured = false;
                    scheduleImageGridVirtualRender(true);
                }
            });

            async function openImageGrid(isSource) {
                const serverSelectId = isSource ? 'sourceServer' : 'targetServer';
                const server = document.getElementById(serverSelectId)?.value;
                const state = isSource ? browseState.source : browseState.target;
                if (!server) {
                    addLogWarning('⚠️ 请先选择服务器');
                    return;
                }
                if (!state.path) {
                    addLogWarning('⚠️ 请先进入一个目录');
                    return;
                }

                const modal = document.getElementById('imageGridModal');
                const container = document.getElementById('imageGridContainer');
                if (!modal || !container) return;
                imageGridSwitching = false;
                imageGridPendingCols = null;
                imageGridVirtualForce = false;
                if (imageGridResumeTimer) {
                    clearTimeout(imageGridResumeTimer);
                    imageGridResumeTimer = 0;
                }
                imageGridEagerMode = false;
                imageGridEagerQueued = false;
                imageGridItems = [];
                imageGridVirtualEnabled = false;
                imageGridRowHeight = 0;
                imageGridRowGap = 0;
                imageGridRowMeasured = false;
                imageGridLastRender = { startIndex: -1, endIndex: -1, cols: 0, total: 0 };
                imageGridActiveServer = server;
                imageGridActiveIsSource = isSource;
                imageGridSelectedPath = '';
                imageGridSelectedName = '';
                imageGridLoadQueue.length = 0;
                imageGridLoadingCount = 0;
                ensureImageGridColsControls();
                modal.style.display = 'block';
	                document.body.style.overflow = 'hidden';
	                ensureImageGridScrollWatcher(modal);
	                applyImageGridColumns(imageGridCols);
	                scheduleImageGridThumbUpdate();
	                container.innerHTML = '<div class="image-grid-empty">加载中...</div>';
	                initImageGridObserver(modal);

                const renderBatch = (items, server) => {
                    const frag = document.createDocumentFragment();
                    for (const item of items) {
                        frag.appendChild(createImageGridCard(item, server, isSource));
                    }
                    container.appendChild(frag);
                };

                const appendImagesChunked = (images, server, chunkSize = 60) => {
                    if (imageGridVirtualEnabled) return;
                    container.innerHTML = '';
                    let idx = 0;
                    const step = () => {
                        const slice = images.slice(idx, idx + chunkSize);
                        renderBatch(slice, server);
                        idx += chunkSize;
                        if (idx < images.length) {
                            requestAnimationFrame(step);
                        }
                    };
                    step();
                };

                const ensureAndRender = async () => {
                    try {
                        const initial = (state.fullItems || []).filter(it => it && !it.is_directory && isImageFile(it.name));
                        imageGridItems = initial.slice();
                        const switchedInitial = setImageGridVirtualEnabled(shouldUseImageGridVirtual(imageGridItems.length));
                        if (imageGridVirtualEnabled) {
                            if (switchedInitial) {
                                container.innerHTML = '';
                            }
                            scheduleImageGridVirtualRender(true);
                        } else if (imageGridItems.length) {
                            appendImagesChunked(imageGridItems, server);
                        } else {
                            container.innerHTML = '<div class="image-grid-empty">加载中...</div>';
                        }


                        if (state.hasMore || state.fullHasMore || !state.fullItems) {
                            let localOffset = state.fullOffset || state.loadedCount || (state.offset || 0);
                            while (true) {
                                const params = new URLSearchParams({
                                    path: state.path,
                                    show_hidden: document.getElementById(isSource ? 'sourceShowHidden' : 'targetShowHidden').checked,
                                    offset: localOffset,
                                    limit: BROWSE_PAGE_SIZE_MAX
                                });
                                const resp = await fetch(`/api/browse/${server}?${params.toString()}`, { cache: 'no-cache' });
                                const data = await resp.json();
                                if (!data.success) break;
                                const pageFiles = (data.files || []).filter(it => it && !it.is_directory && isImageFile(it.name));
                                if (pageFiles.length) {
                                    imageGridItems.push(...pageFiles);
                                    const switched = setImageGridVirtualEnabled(shouldUseImageGridVirtual(imageGridItems.length));
                                    if (imageGridVirtualEnabled) {
                                        if (switched) {
                                            container.innerHTML = '';
                                        }
                                        scheduleImageGridVirtualRender(switched);
                                    } else {
                                        if (!container.innerHTML || container.innerHTML.includes('加载中')) {
                                            container.innerHTML = '';
                                        }
                                        renderBatch(pageFiles, server);
                                    }
                                }
                                state.fullItems = (state.fullItems || []).concat(data.files || []);
                                state.total = data.total_count || data.file_count || state.total || 0;
                                state.fullOffset = data.next_offset ?? (localOffset + (data.files || []).length);
                                state.fullHasMore = data.has_more;
                                state.loadedCount = Math.max(state.loadedCount || 0, state.fullOffset || 0);
                                localOffset = state.fullOffset || localOffset + (data.files || []).length;
                                if (!data.has_more) break;
                            }
                        }

                        const imgsCount = imageGridItems.length;
                        if (!imgsCount) {
                            container.innerHTML = '<div class="image-grid-empty">当前目录没有图片</div>';
                        } else {
                            addLogInfo(`🖼️ 已展开 ${imgsCount} 张图片`);
                        }
                    } catch (e) {
                        container.innerHTML = `<div class="image-grid-empty">加载失败: ${escapeHtml(e.message || e)}</div>`;
                        addLogError('❌ 图片网格加载失败: ' + (e.message || e));
                    }
                };

                ensureAndRender();
            }

            async function deleteSelectedImageGrid() {
                if (!imageGridSelectedPath) return false;
                if (!imageGridActiveServer) {
                    addLogWarning('⚠️ 请先选择服务器');
                    return false;
                }
                const path = imageGridSelectedPath;
                const ok = await deletePathsDirect(imageGridActiveServer, [path], imageGridActiveIsSource);
                if (!ok) return false;
                removeImageGridItem(path);
                return true;
            }

            function closeImageGrid() {
                const modal = document.getElementById('imageGridModal');
                const container = document.getElementById('imageGridContainer');
                if (modal) modal.style.display = 'none';
                if (container) container.innerHTML = '';
                document.body.style.overflow = '';
                imageGridSwitching = false;
                if (imageGridResumeTimer) {
                    clearTimeout(imageGridResumeTimer);
                    imageGridResumeTimer = 0;
                }
                if (imageGridObserver) {
                    imageGridObserver.disconnect();
                    imageGridObserver = null;
                }
                if (imageGridColsApplyRaf) {
                    try { cancelAnimationFrame(imageGridColsApplyRaf); } catch (_) {}
                    imageGridColsApplyRaf = 0;
                }
                if (imageGridColsRaf) {
                    try { cancelAnimationFrame(imageGridColsRaf); } catch (_) {}
                    imageGridColsRaf = 0;
                }
                if (imageGridResizeRaf) {
                    try { cancelAnimationFrame(imageGridResizeRaf); } catch (_) {}
                    imageGridResizeRaf = 0;
                }
                if (imageGridRenderRaf) {
                    try { cancelAnimationFrame(imageGridRenderRaf); } catch (_) {}
                    imageGridRenderRaf = 0;
                }
                if (imageGridScrollRaf) {
                    try { cancelAnimationFrame(imageGridScrollRaf); } catch (_) {}
                    imageGridScrollRaf = 0;
                }
                imageGridThumbWidth = 0;
                imageGridEagerMode = false;
                imageGridEagerQueued = false;
                imageGridLoadQueue.length = 0;
                imageGridLoadingCount = 0;
                imageGridItems = [];
                imageGridVirtualEnabled = false;
                imageGridRowHeight = 0;
                imageGridRowGap = 0;
                imageGridRowMeasured = false;
                imageGridLastRender = { startIndex: -1, endIndex: -1, cols: 0, total: 0 };
                imageGridActiveServer = '';
                imageGridActiveIsSource = true;
                imageGridSelectedPath = '';
                imageGridSelectedName = '';
            }

            const EDITOR_THEME_KEY = 'turbofile_editor_theme';

            function applyEditorTheme(mode) {
                const modal = document.getElementById('editorModal');
                if (!modal) return;
                modal.classList.toggle('editor-theme-light', mode === 'light');
            }

            function toggleEditorTheme() {
                const current = localStorage.getItem(EDITOR_THEME_KEY) || 'dark';
                const next = current === 'light' ? 'dark' : 'light';
                localStorage.setItem(EDITOR_THEME_KEY, next);
                applyEditorTheme(next);
            }

            function openEditorModal(server, path, title, content) {
                const modal = document.getElementById('editorModal');
                const savedTheme = localStorage.getItem(EDITOR_THEME_KEY) || 'dark';
                applyEditorTheme(savedTheme);
                modal.querySelector('.modal-title').textContent = title;
                const ta = modal.querySelector('#editorTextarea');
                ta.value = content;
                ta.dataset.server = server;
                ta.dataset.path = path;
                const findInput = document.getElementById('findInput');
                const replaceInput = document.getElementById('replaceInput');
                if (findInput) findInput.value = '';
                if (replaceInput) replaceInput.value = '';
                const countEl = document.getElementById('findCount');
                if (countEl) countEl.textContent = '';
                syncMinimap();
                updateEditorLineNumbers();
                renderFindHighlights();
                syncEditorScroll();
                modal.style.display = 'block';
                document.body.style.overflow = 'hidden';
                hideFindReplace();
                startEditorScrollSync();
                ta.focus();
            }

            function compareFromSelection() {
                const srcServer = document.getElementById('sourceServer').value;
                const tgtServer = document.getElementById('targetServer').value;
                const leftSelection = selectedSourceFiles.filter(f => !f.is_directory).map(f => ({ ...f, server: srcServer }));
                const rightSelection = selectedTargetFiles.filter(f => !f.is_directory).map(f => ({ ...f, server: tgtServer }));
                const all = [...leftSelection, ...rightSelection];
                if (all.length !== 2) {
                    addLogWarning('⚠️ 请按Ctrl选中两个文件后再点击对比（可跨服务器）');
                    return;
                }
                if (!all[0].server || !all[1].server) {
                    addLogWarning('⚠️ 请先选择左右服务器');
                    return;
                }
                const [a, b] = all;
                const extA = (a.name.lastIndexOf('.') >= 0) ? a.name.slice(a.name.lastIndexOf('.')).toLowerCase() : '';
                const extB = (b.name.lastIndexOf('.') >= 0) ? b.name.slice(b.name.lastIndexOf('.')).toLowerCase() : '';
                if (extA !== extB) {
                    addLogWarning('⚠️ 仅支持相同类型的文件进行对比');
                    return;
                }
                performCompare(
                    { server: a.server, path: a.path, name: a.name, ext: extA },
                    { server: b.server, path: b.path, name: b.name, ext: extB }
                );
            }

            async function performCompare(left, right) {
                try {
                    addLogInfo(`🧐 正在对比：${left.name} ⇆ ${right.name}`);
                    const resp = await fetch('/api/compare_files', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            server_a: left.server,
                            path_a: left.path,
                            server_b: right.server,
                            path_b: right.path
                        })
                    });
                    const data = await resp.json();
                    if (!data.success) {
                        addLogError(`❌ 对比失败: ${data.error || '未知错误'}`);
                        return;
                    }
                    showDiffModal(data.lines || [], left, right);
                } catch (e) {
                    addLogError(`❌ 对比异常: ${e.message || e}`);
                }
            }

            function showDiffModal(lines, leftMeta, rightMeta) {
                const modal = document.getElementById('diffModal');
                const rows = document.getElementById('diffRows');
                const leftPath = document.getElementById('diffLeftPath');
                const rightPath = document.getElementById('diffRightPath');
                const summary = document.getElementById('diffSummary');
                if (!modal || !rows) return;

                rows.innerHTML = '';
                leftPath.textContent = leftMeta ? `${leftMeta.name} (${leftMeta.server})` : '';
                rightPath.textContent = rightMeta ? `${rightMeta.name} (${rightMeta.server})` : '';

                let addCount = 0, delCount = 0, repCount = 0;
                (lines || []).forEach(line => {
                    const tag = line.tag || 'equal';
                    if (tag === 'insert') addCount++;
                    else if (tag === 'delete') delCount++;
                    else if (tag === 'replace') repCount++;

                    const pair = document.createElement('div');
                    pair.className = 'diff-line';
                    pair.classList.add(tag);

                    const leftSide = document.createElement('div');
                    leftSide.className = 'diff-side left';
                    const lno = document.createElement('div');
                    lno.className = 'diff-lineno';
                    lno.textContent = line.left_no ? line.left_no : '';
                    const lcode = document.createElement('div');
                    lcode.className = 'diff-code';
                    lcode.textContent = (line.left ?? '');
                    leftSide.appendChild(lno);
                    leftSide.appendChild(lcode);

                    const rightSide = document.createElement('div');
                    rightSide.className = 'diff-side right';
                    const rno = document.createElement('div');
                    rno.className = 'diff-lineno';
                    rno.textContent = line.right_no ? line.right_no : '';
                    const rcode = document.createElement('div');
                    rcode.className = 'diff-code';
                    rcode.textContent = (line.right ?? '');
                    rightSide.appendChild(rno);
                    rightSide.appendChild(rcode);

                    pair.appendChild(leftSide);
                    pair.appendChild(rightSide);
                    rows.appendChild(pair);
                });

                if (summary) {
                    summary.textContent = `新增 ${addCount}，删除 ${delCount}，修改 ${repCount}`;
                }

                modal.style.display = 'block';
                document.body.style.overflow = 'hidden';
            }

            function closeDiffModal() {
                const modal = document.getElementById('diffModal');
                const rows = document.getElementById('diffRows');
                if (modal) modal.style.display = 'none';
                if (rows) rows.innerHTML = '';
                document.body.style.overflow = '';
            }

            function setupImageWheelZoom() {
                const modal = document.getElementById('imagePreviewModal');
                const img = modal ? modal.querySelector('img') : null;
                if (!modal || !img) return;
                let dragging = false;
                let startX = 0;
                let startY = 0;
                let lastX = 0;
                let lastY = 0;
                let rafPending = false;

                modal.addEventListener('wheel', (e) => {
                    if (modal.style.display === 'none') return;
                    e.preventDefault();
                    const delta = e.deltaY > 0 ? -0.1 : 0.1;
                    imageZoom = Math.min(5, Math.max(0.2, imageZoom + delta));
                    applyImageTransform(img);
                }, { passive: false });

                modal.addEventListener('mousedown', (e) => {
                    if (modal.style.display === 'none') return;
                    if (e.target.closest('#imagePreviewCloseBtn') ||
                        e.target.closest('#imagePrevBtn') ||
                        e.target.closest('#imageNextBtn')) {
                        return;
                    }
                    dragging = true;
                    startX = e.clientX - imageOffsetX;
                    startY = e.clientY - imageOffsetY;
                    lastX = imageOffsetX;
                    lastY = imageOffsetY;
                    e.preventDefault();
                });

                modal.addEventListener('mousemove', (e) => {
                    if (!dragging) return;
                    lastX = e.clientX - startX;
                    lastY = e.clientY - startY;
                    if (!rafPending) {
                        rafPending = true;
                        requestAnimationFrame(() => {
                            imageOffsetX = lastX;
                            imageOffsetY = lastY;
                            applyImageTransform(img);
                            rafPending = false;
                        });
                    }
                });

                ['mouseup', 'mouseleave'].forEach(ev => {
                    modal.addEventListener(ev, () => { dragging = false; });
                });
            }
            function closeEditorModal() {
                const modal = document.getElementById('editorModal');
                if (modal) modal.style.display = 'none';
                document.body.style.overflow = '';
                stopEditorScrollSync();
            }
            async function saveEditorContent() {
                const ta = document.querySelector('#editorModal textarea');
                if (!ta) return;
                const server = ta.dataset.server;
                const path = ta.dataset.path;
                const content = ta.value;
                try {
                    const resp = await fetch('/api/file/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ server, path, content })
                    });
                    const data = await resp.json();
                    if (data.success) {
                        addLogInfo('💾 已保存: ' + path);
                        previewCacheSet(server, path, 'text', content);
                        closeEditorModal();
                    } else {
                        addLogError('保存失败: ' + (data.error || '未知错误'));
                    }
                } catch (e) {
                    addLogError('保存失败: ' + (e.message || e));
                }
            }

            let previewEditorBindingsReady = false;
            function bindPreviewAndEditorEvents() {
                if (previewEditorBindingsReady) return;
                previewEditorBindingsReady = true;

                const btn = document.getElementById('imagePreviewCloseBtn');
                if (btn) btn.addEventListener('click', closeImageModal, { passive: true });
                const prevBtn = document.getElementById('imagePrevBtn');
                const nextBtn = document.getElementById('imageNextBtn');
                if (prevBtn) prevBtn.addEventListener('click', () => showImageAt(ImageViewer.index - 1));
                if (nextBtn) nextBtn.addEventListener('click', () => showImageAt(ImageViewer.index + 1));

                const ta = document.getElementById('editorTextarea');
                if (ta) {
                    ta.addEventListener('input', () => {
                        updateEditorLineNumbers();
                        renderFindHighlights();
                    });
                    ta.addEventListener('scroll', syncEditorScroll);
                    ta.addEventListener('wheel', () => {
                        requestAnimationFrame(syncEditorScroll);
                    }, { passive: true });
                    const gutter = document.getElementById('editorGutter');
                    if (gutter) {
                        gutter.addEventListener('wheel', (e) => {
                            ta.scrollTop += e.deltaY;
                            syncEditorScroll();
                            e.preventDefault();
                        }, { passive: false });
                    }
                    updateEditorLineNumbers();
                    renderFindHighlights();
                    syncEditorScroll();
                }

                document.addEventListener('keydown', (e) => {
                    const modalVisible = document.getElementById('editorModal') && document.getElementById('editorModal').style.display !== 'none';
                    if (!modalVisible) return;
                    if (document.activeElement && document.activeElement.id === 'editorTextarea' && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
                        return;
                    }
                    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
                        e.preventDefault();
                        showFindReplace(false);
                    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'h') {
                        e.preventDefault();
                        showFindReplace(true);
                    }
                });
            }

            function syncMinimap() {

            }

            function syncMinimapHighlight() {

            }

            let editorScrollRaf = null;

            function startEditorScrollSync() {
                if (editorScrollRaf) return;
                const tick = () => {
                    const modal = document.getElementById('editorModal');
                    if (!modal || modal.style.display === 'none') {
                        editorScrollRaf = null;
                        return;
                    }
                    syncEditorScroll();
                    editorScrollRaf = requestAnimationFrame(tick);
                };
                editorScrollRaf = requestAnimationFrame(tick);
            }

            function stopEditorScrollSync() {
                if (!editorScrollRaf) return;
                cancelAnimationFrame(editorScrollRaf);
                editorScrollRaf = null;
            }

            function updateEditorLineNumbers() {
                const ta = document.getElementById('editorTextarea');
                const lineBox = document.getElementById('editorLineNumbers');
                if (!ta || !lineBox) return;
                const text = ta.value || '';
                const lines = text.split('\n');
                const count = Math.max(1, lines.length);
                let nums = '';
                for (let i = 1; i <= count; i++) {
                    nums += i + (i === count ? '' : '\n');
                }
                lineBox.textContent = nums;
            }

            function syncEditorScroll() {
                const ta = document.getElementById('editorTextarea');
                const layer = document.getElementById('editorHighlightLayer');
                const lineBox = document.getElementById('editorLineNumbers');
                if (!ta) return;
                if (layer) {
                    layer.style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
                }
                if (lineBox) {
                    lineBox.style.marginTop = `${-ta.scrollTop}px`;
                }
            }

            function renderFindHighlights() {
                const layer = document.getElementById('editorHighlightLayer');
                const ta = document.getElementById('editorTextarea');
                const findInput = document.getElementById('findInput');
                const query = findInput ? findInput.value : '';
                const countEl = document.getElementById('findCount');
                if (!layer || !ta) return;
                const text = ta.value || '';
                if (!query) {
                    layer.innerHTML = escapeHtml(text);
                    if (countEl) countEl.textContent = '';
                    syncEditorScroll();
                    return;
                }
                const safeQuery = query.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
                const regex = new RegExp(safeQuery, 'g');
                let matchCount = 0;
                text.replace(regex, () => { matchCount++; return ''; });
                layer.innerHTML = escapeHtml(text);
                if (countEl) {
                    countEl.textContent = matchCount > 0 ? `${matchCount} 条` : '0 条';
                }
                syncEditorScroll();
            }


            function showFindReplace(withReplace) {
                const bar = document.getElementById('findReplaceBar');
                const findInput = document.getElementById('findInput');
                const replaceInput = document.getElementById('replaceInput');
                const replaceBtn = document.getElementById('replaceBtn');
                const replaceAllBtn = document.getElementById('replaceAllBtn');
                if (!bar) return;
                bar.style.display = 'flex';
                if (withReplace) {
                    replaceInput.style.display = 'inline-block';
                    replaceBtn.style.display = 'inline-block';
                    replaceAllBtn.style.display = 'inline-block';
                } else {
                    replaceInput.style.display = 'none';
                    replaceBtn.style.display = 'none';
                    replaceAllBtn.style.display = 'none';
                }
                setTimeout(() => findInput && findInput.focus(), 0);
            }

            function hideFindReplace() {
                const bar = document.getElementById('findReplaceBar');
                const findInput = document.getElementById('findInput');
                const replaceInput = document.getElementById('replaceInput');
                const countEl = document.getElementById('findCount');
                if (findInput) findInput.value = '';
                if (replaceInput) replaceInput.value = '';
                if (countEl) countEl.textContent = '';
                renderFindHighlights();
                if (bar) bar.style.display = 'none';
            }

            function findNext() {
                const ta = document.getElementById('editorTextarea');
                const query = document.getElementById('findInput').value;
                renderFindHighlights();
                if (!ta || !query) return;
                const start = ta.selectionEnd;
                const idx = ta.value.indexOf(query, start);
                if (idx !== -1) {
                    ta.focus();
                    ta.setSelectionRange(idx, idx + query.length);
                    scrollToSelectionCenter();
                    return;
                }

                const wrapIdx = ta.value.indexOf(query, 0);
                if (wrapIdx !== -1) {
                    ta.focus();
                    ta.setSelectionRange(wrapIdx, wrapIdx + query.length);
                    scrollToSelectionCenter();
                }
            }

            function findPrev() {
                const ta = document.getElementById('editorTextarea');
                const query = document.getElementById('findInput').value;
                renderFindHighlights();
                if (!ta || !query) return;
                const start = ta.selectionStart - 1;
                const idx = ta.value.lastIndexOf(query, start);
                if (idx !== -1) {
                    ta.focus();
                    ta.setSelectionRange(idx, idx + query.length);
                    scrollToSelectionCenter();
                    return;
                }
                const wrapIdx = ta.value.lastIndexOf(query);
                if (wrapIdx !== -1) {
                    ta.focus();
                    ta.setSelectionRange(wrapIdx, wrapIdx + query.length);
                    scrollToSelectionCenter();
                }
            }

            function scrollToSelectionCenter() {
                const ta = document.getElementById('editorTextarea');
                if (!ta) return;
                const selStart = ta.selectionStart || 0;
                const beforeText = ta.value.slice(0, selStart);
                const lines = beforeText.split('\n');
                const lineHeight = parseFloat(getComputedStyle(ta).lineHeight || '16');
                const targetTop = (lines.length - 1) * lineHeight;
                const centerOffset = ta.clientHeight / 2;
                ta.scrollTop = Math.max(0, targetTop - centerOffset);
            }

            function replaceOne() {
                const ta = document.getElementById('editorTextarea');
                const query = document.getElementById('findInput').value;
                const replacement = document.getElementById('replaceInput').value;
                if (!ta || !query) return;
                const selText = ta.value.substring(ta.selectionStart, ta.selectionEnd);
                if (selText === query) {
                    const before = ta.value.substring(0, ta.selectionStart);
                    const after = ta.value.substring(ta.selectionEnd);
                    const pos = before.length + replacement.length;
                    ta.value = before + replacement + after;
                    ta.setSelectionRange(pos - replacement.length, pos);
                    updateEditorLineNumbers();
                    syncMinimap();
                }
                findNext();
                renderFindHighlights();
            }

            function replaceAll() {
                const ta = document.getElementById('editorTextarea');
                const query = document.getElementById('findInput').value;
                const replacement = document.getElementById('replaceInput').value;
                if (!ta || !query) return;
                ta.value = ta.value.split(query).join(replacement);
                updateEditorLineNumbers();
                syncMinimap();
                renderFindHighlights();
            }

        document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    closeImageModal();
                    closeEditorModal();
                    closeDiffModal();
                }
                const modal = document.getElementById('imagePreviewModal');
                const visible = modal && modal.style.display !== 'none';
                const gridModal = document.getElementById('imageGridModal');
                const gridVisible = gridModal && gridModal.style.display !== 'none';
                if (visible && ImageViewer.items.length > 0) {
                    if (e.key === 'ArrowLeft') {
                        e.preventDefault();
                        showImageAt(ImageViewer.index - 1);
                    } else if (e.key === 'ArrowRight') {
                        e.preventDefault();
                        showImageAt(ImageViewer.index + 1);
                    }
                }

                if (e.key === 'Delete') {
                    if (_isEditableElement(e.target)) return;
                    if (visible && ImageViewer.items.length > 0) {
                        e.preventDefault();
                        deleteCurrentPreviewImage();
                        return;
                    }
                    if (gridVisible) {
                        if (imageGridSelectedPath) {
                            e.preventDefault();
                            deleteSelectedImageGrid();
                        }
                        return;
                    }
                    const activePanel = document.activeElement && document.activeElement.closest && document.activeElement.closest('.file-browser');
                    let isSource = true;
                    if (activePanel && activePanel.id === 'targetFileBrowser') {
                        isSource = false;
                    } else if (activePanel && activePanel.id === 'sourceFileBrowser') {
                        isSource = true;
                    } else {
                        isSource = lastActivePanel !== 'target';
                    }
                    const selected = isSource ? selectedSourceFiles : selectedTargetFiles;
                    if (selected && selected.length) {
                        e.preventDefault();
                        deleteSelected(isSource ? 'source' : 'target');
                    }
                }

                if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
                    if (_isEditableElement(e.target)) return;
                    e.preventDefault();

                    const activePanel = document.activeElement && document.activeElement.closest && document.activeElement.closest('.file-browser');
                    if (activePanel && activePanel.id === 'targetFileBrowser') {
                        selectAll(false);
                    } else if (activePanel && activePanel.id === 'sourceFileBrowser') {
                        selectAll(true);
                    } else {

                        if (lastActivePanel === 'target') {
                            selectAll(false);
                        } else {
                            selectAll(true);
                        }
                    }
                }
            });


        function bindContextMenus() {
            const sourceContainer = document.getElementById('sourceFileBrowser');
            const targetContainer = document.getElementById('targetFileBrowser');
            const contextMenu = document.getElementById('fileContextMenu');
            const renameAction = contextMenu ? contextMenu.querySelector('[data-action="rename"]') : null;
            const newFileAction = contextMenu ? contextMenu.querySelector('[data-action="new-file"]') : null;
            const newFolderAction = contextMenu ? contextMenu.querySelector('[data-action="new-folder"]') : null;
            const runAction = contextMenu ? contextMenu.querySelector('[data-action="run"]') : null;
            const compareAction = contextMenu ? contextMenu.querySelector('[data-action="compare"]') : null;
            const sizeAction = contextMenu ? contextMenu.querySelector('[data-action="size"]') : null;
            const compressAction = contextMenu ? contextMenu.querySelector('[data-action="compress"]') : null;
            const extractAction = contextMenu ? contextMenu.querySelector('[data-action="extract"]') : null;
            const downloadWindowsAction = contextMenu ? contextMenu.querySelector('[data-action="download-windows"]') : null;
            let contextState = { isSource: true, targetRow: null };

            function hideContextMenu() {
                if (!contextMenu) return;
                contextMenu.style.display = 'none';
                contextMenu.dataset.visible = 'false';
            }

            function selectRowForContext(row, isSource) {
                if (!row) return;
                const alreadySelected = row.classList.contains('selected');

                if (alreadySelected) {
                    lastActivePanel = isSource ? 'source' : 'target';
                    updateSelectionInfo();
                    return;
                }


                row.classList.add('selected');
                const item = {
                    path: row.dataset.path,
                    name: row.dataset.name,
                    is_directory: String(row.dataset.isDirectory).toLowerCase() === 'true'
                };
                if (isSource) {
                    if (!selectedSourceFiles.find(f => f.path === item.path)) {
                        selectedSourceFiles.push(item);
                    }
                    lastSelectedIndex.source = Number(row.dataset.idx || 0);
                } else {
                    if (!selectedTargetFiles.find(f => f.path === item.path)) {
                        selectedTargetFiles.push(item);
                    }
                    lastSelectedIndex.target = Number(row.dataset.idx || 0);
                }
                lastActivePanel = isSource ? 'source' : 'target';
                updateSelectionInfo();
            }

            function startInlineRename(row, isSource) {
                if (!row || row.dataset.editing === 'true') return;
                const nameSpan = row.querySelector('.file-name');
                if (!nameSpan) return;
                const original = nameSpan.textContent;
                const input = document.createElement('input');
                input.type = 'text';
                input.value = original;
                input.className = 'inline-rename-input';
                input.setAttribute('autocomplete', 'off');
                input.setAttribute('spellcheck', 'false');
                const info = row.querySelector('.file-info');
                const details = row.querySelector('.file-details');
                if (info) {
                    const infoWidth = info.getBoundingClientRect().width;
                    const detailsWidth = details ? details.getBoundingClientRect().width : 0;
                    const maxWidth = Math.max(120, Math.floor(infoWidth - detailsWidth - 24));
                    const estimated = Math.min(maxWidth, Math.max(120, Math.ceil(original.length * 8 + 24)));
                    input.style.width = `${estimated}px`;
                }
                row.dataset.editing = 'true';
                nameSpan.replaceWith(input);
                input.focus();
                input.select();

                const cleanup = () => {
                    if (row.dataset.editing !== 'true') return;
                    const span = document.createElement('span');
                    span.className = 'file-name';
                    span.textContent = original;
                    input.replaceWith(span);
                    row.dataset.editing = 'false';
                };

                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        const newName = input.value.trim();
                        if (!newName || newName === original) {
                            cleanup();
                            return;
                        }
                        const server = document.getElementById(isSource ? 'sourceServer' : 'targetServer').value;
                        const path = row.dataset.path;
                        renameFile(isSource ? 'source' : 'target', server, path, newName);
                        cleanup();
                    } else if (e.key === 'Escape') {
                        e.preventDefault();
                        cleanup();
                    }
                });

                ['mousedown', 'click'].forEach(ev => input.addEventListener(ev, (e) => e.stopPropagation()));
                input.addEventListener('blur', cleanup);
            }

            function startInlineCreate(isSource) {
                const { containerId } = getPanelConfig(isSource);
                const container = document.getElementById(containerId);
                const server = document.getElementById(isSource ? 'sourceServer' : 'targetServer').value;
                const currentPath = getActivePath(isSource);
                if (!container || !server || !currentPath) return;


                const existing = container.querySelector('.file-item.temp-new');
                if (existing) existing.remove();

                const row = document.createElement('div');
                row.className = 'file-item selectable temp-new';
                row.innerHTML = `
                    <i class="bi bi-folder-plus text-warning"></i>
                    <div class="file-info">
                        <input class="form-control form-control-sm" type="text" placeholder="新建文件夹" style="width: 100%; padding: 1px 4px; height: 22px; font-size: 0.9rem; box-sizing: border-box; display: inline-block;" />
                    </div>
                `;
                const backRow = container.querySelector('.file-item[title*="返回"]');
                if (backRow && backRow.nextSibling) {
                    container.insertBefore(row, backRow.nextSibling);
                } else {
                    container.insertBefore(row, container.firstChild);
                }

                const input = row.querySelector('input');
                if (input) {
                    input.focus();
                    input.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter') {
                            e.preventDefault();
                            const name = input.value.trim();
                            if (!name) {
                                row.remove();
                                return;
                            }
                            createFolder(isSource ? 'source' : 'target', server, currentPath, name);
                            row.remove();
                        } else if (e.key === 'Escape') {
                            e.preventDefault();
                            row.remove();
                        }
                    });
                    input.addEventListener('blur', () => row.remove());
                }
            }

            function startInlineCreateFile(isSource) {
                const { containerId } = getPanelConfig(isSource);
                const container = document.getElementById(containerId);
                const server = document.getElementById(isSource ? 'sourceServer' : 'targetServer').value;
                const currentPath = getActivePath(isSource);
                if (!container || !server || !currentPath) return;

                const existing = container.querySelector('.file-item.temp-new-file');
                if (existing) existing.remove();

                const row = document.createElement('div');
                row.className = 'file-item selectable temp-new-file';
                row.innerHTML = `
                    <i class="bi bi-file-earmark-plus text-primary"></i>
                    <div class="file-info">
                        <input class="form-control form-control-sm" type="text" placeholder="新建文件" style="width: 100%; padding: 1px 4px; height: 22px; font-size: 0.9rem; box-sizing: border-box; display: inline-block;" />
                    </div>
                `;
                const backRow = container.querySelector('.file-item[title*="返回"]');
                if (backRow && backRow.nextSibling) {
                    container.insertBefore(row, backRow.nextSibling);
                } else {
                    container.insertBefore(row, container.firstChild);
                }

                const input = row.querySelector('input');
                if (input) {
                    input.focus();
                    const cleanup = () => row.remove();
                    input.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter') {
                            e.preventDefault();
                            const name = (input.value || '').trim();
                            if (!name) {
                                cleanup();
                                return;
                            }
                            createFile(isSource ? 'source' : 'target', server, currentPath, name);
                            cleanup();
                        } else if (e.key === 'Escape') {
                            e.preventDefault();
                            cleanup();
                        }
                    });
                    input.addEventListener('blur', () => cleanup(), { once: true });
                }
            }

            function positionContextMenu(x, y) {
                if (!contextMenu) return;
                const rect = contextMenu.getBoundingClientRect();
                const menuWidth = rect.width || 180;
                const menuHeight = rect.height || 90;
                const padding = 8;
                let left = x;
                let top = y;

                if (left + menuWidth > window.innerWidth - padding) {
                    left = window.innerWidth - menuWidth - padding;
                }
                if (top + menuHeight > window.innerHeight - padding) {
                    top = window.innerHeight - menuHeight - padding;
                }

                const finalLeft = Math.max(padding, left);
                const finalTop = Math.max(padding, top);

                contextMenu.style.left = `${finalLeft}px`;
                contextMenu.style.top = `${finalTop}px`;

                if (typeof window !== 'undefined') {
                    window.downloadWinAnchor = { x: finalLeft, y: finalTop };
                }
            }

            function showContextMenu(e, isSource) {
                const containerId = isSource ? 'sourceFileBrowser' : 'targetFileBrowser';
                const server = document.getElementById(isSource ? 'sourceServer' : 'targetServer').value;
                const container = document.getElementById(containerId);

                if (!contextMenu || !container || !e.target.closest(`#${containerId}`)) return;
                if (!server) return;

                e.preventDefault();
                hideContextMenu();

                const row = e.target.closest('.file-item.selectable');
                contextState = { isSource, targetRow: row || null };

                if (row) {
                    selectRowForContext(row, isSource);
                    if (renameAction) renameAction.classList.remove('disabled');
                } else if (renameAction) {
                    renameAction.classList.add('disabled');
                }

                const canRun = row && String(row.dataset.isDirectory).toLowerCase() !== 'true' && isRunnableFileName(row.dataset.name);
                if (runAction) {
                    if (canRun) {
                        runAction.classList.remove('disabled');
                    } else {
                        runAction.classList.add('disabled');
                    }
                }
                const totalSelectedFiles = selectedSourceFiles.filter(f => !f.is_directory).length + selectedTargetFiles.filter(f => !f.is_directory).length;
                if (compareAction) {
                    if (totalSelectedFiles >= 2) {
                        compareAction.classList.remove('disabled');
                    } else {
                        compareAction.classList.add('disabled');
                    }
                }

                const currentPath = getActivePath(isSource);
                if (newFileAction) {
                    if (currentPath) {
                        newFileAction.classList.remove('disabled');
                    } else {
                        newFileAction.classList.add('disabled');
                    }
                }
                if (newFolderAction) {
                    if (currentPath) {
                        newFolderAction.classList.remove('disabled');
                    } else {
                        newFolderAction.classList.add('disabled');
                    }
                }

                if (sizeAction) {
                    if (row) {
                        sizeAction.classList.remove('disabled');
                    } else {
                        sizeAction.classList.add('disabled');
                    }
                }

                if (compressAction) {
                    if (row) {
                        compressAction.classList.remove('disabled');
                    } else {
                        compressAction.classList.add('disabled');
                    }
                }
                if (extractAction) {
                    const canExtract = row && isArchiveFile(row.dataset.name || '');
                    if (canExtract) {
                        extractAction.classList.remove('disabled');
                    } else {
                        extractAction.classList.add('disabled');
                    }
                }

                if (downloadWindowsAction) {
                    const leftSelected = selectedSourceFiles.length > 0;
                    const rightSelected = selectedTargetFiles.length > 0;
                    const hasWindows = (() => {
                        try {
                            return Object.keys(SERVERS_DATA || {}).some(ip => SERVERS_DATA[ip] && SERVERS_DATA[ip].os_type === 'windows');
                        } catch (_) {
                            return false;
                        }
                    })();
                    const canDownload = hasWindows && (leftSelected || rightSelected) && !(leftSelected && rightSelected);
                    if (canDownload) {
                        downloadWindowsAction.classList.remove('disabled');
                    } else {
                        downloadWindowsAction.classList.add('disabled');
                    }
                }

                contextMenu.style.display = 'block';
                positionContextMenu(e.clientX, e.clientY);
                contextMenu.dataset.visible = 'true';
            }

            if (renameAction) {
                renameAction.addEventListener('click', () => {
                    if (renameAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    const { isSource, targetRow } = contextState;
                    if (targetRow) {
                        startInlineRename(targetRow, isSource);
                    } else {
                        showRenameDialog(isSource ? 'source' : 'target');
                    }
                });
            }

            if (newFolderAction) {
                newFolderAction.addEventListener('click', () => {
                    if (newFolderAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    startInlineCreate(contextState.isSource);
                });
            }

            if (newFileAction) {
                newFileAction.addEventListener('click', () => {
                    if (newFileAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    startInlineCreateFile(contextState.isSource);
                });
            }

            if (compareAction) {
                compareAction.addEventListener('click', () => {
                    if (compareAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    compareFromSelection();
                });
            }

            if (runAction) {
                runAction.addEventListener('click', () => {
                    if (runAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    const { isSource, targetRow } = contextState;
                    if (!targetRow) {
                        addLogWarning('⚠️ 请选择要运行的 .py 或 .sh 文件');
                        return;
                    }
                    const isDir = String(targetRow.dataset.isDirectory).toLowerCase() === 'true';
                    const fileName = targetRow.dataset.name || '';
                    if (isDir || !isRunnableFileName(fileName)) {
                        addLogWarning('⚠️ 仅支持运行 .py 或 .sh 文件');
                        return;
                    }
                    const server = document.getElementById(isSource ? 'sourceServer' : 'targetServer').value;
                    runFileOnServer(server, targetRow.dataset.path, fileName);
                });
            }

            if (sizeAction) {
                sizeAction.addEventListener('click', () => {
                    if (sizeAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    const { isSource, targetRow } = contextState;
                    if (!targetRow) {
                        addLogWarning('⚠️ 请先选择文件或文件夹');
                        return;
                    }
                    const server = document.getElementById(isSource ? 'sourceServer' : 'targetServer').value;
                    const name = targetRow.dataset.name || '';
                    computeSizeOnServer(server, targetRow.dataset.path, name);
                });
            }

            if (compressAction) {
                compressAction.addEventListener('click', () => {
                    if (compressAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    const { isSource, targetRow } = contextState;
                    if (!targetRow) {
                        addLogWarning('⚠️ 请先选择文件或文件夹');
                        return;
                    }
                    const server = document.getElementById(isSource ? 'sourceServer' : 'targetServer').value;
                    const name = targetRow.dataset.name || '';
                    addLogInfo(`🗜️ 正在压缩: ${name}`);
                    computeSizeOnServer(server, targetRow.dataset.path, name);
                    compressPathOnServer(server, targetRow.dataset.path, name);
                });
            }

            if (extractAction) {
                extractAction.addEventListener('click', () => {
                    if (extractAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    const { isSource, targetRow } = contextState;
                    if (!targetRow) {
                        addLogWarning('⚠️ 请先选择压缩文件');
                        return;
                    }
                    const server = document.getElementById(isSource ? 'sourceServer' : 'targetServer').value;
                    const name = targetRow.dataset.name || '';
                    if (!isArchiveFile(name)) {
                        addLogWarning('⚠️ 仅支持 zip/tar/tgz/tar.gz/tar.bz2/tar.xz');
                        return;
                    }
                    addLogInfo(`📂 正在解压: ${name}`);
                    extractArchiveOnServer(server, targetRow.dataset.path, name);
                });
            }


            if (downloadWindowsAction) {
                downloadWindowsAction.addEventListener('click', () => {
                    if (downloadWindowsAction.classList.contains('disabled')) return;
                    hideContextMenu();
                    openDownloadToWindowsModal();
                });
            }

            if (sourceContainer) {
                sourceContainer.addEventListener('contextmenu', (e) => showContextMenu(e, true));
                sourceContainer.addEventListener('scroll', hideContextMenu, { passive: true });
            }
            if (targetContainer) {
                targetContainer.addEventListener('contextmenu', (e) => showContextMenu(e, false));
                targetContainer.addEventListener('scroll', hideContextMenu, { passive: true });
            }

            document.addEventListener('click', (evt) => {
                if (contextMenu && contextMenu.dataset.visible === 'true' && !evt.target.closest('#fileContextMenu')) {
                    hideContextMenu();
                }
            });
            document.addEventListener('scroll', hideContextMenu, true);
            window.addEventListener('resize', hideContextMenu);
            document.addEventListener('keydown', (evt) => {
                if (evt.key === 'Escape') {
                    hideContextMenu();
                }
            });


            function bindEmptyClick(container, isSource) {
                if (!container) return;
                container.addEventListener('click', (e) => {
                    const row = e.target.closest('.file-item.selectable');
                    if (row) return;
                    clearSelectionsForPanel(isSource);
                    updateSelectionInfo();
                    lastActivePanel = isSource ? 'source' : 'target';
                });
            }
            bindEmptyClick(sourceContainer, true);
            bindEmptyClick(targetContainer, false);
        }


            document.addEventListener('DOMContentLoaded', function() {
                addLogInfo('🚀 TurboFile 极速传文件传输系统已启动');
                addLogInfo('📋 请选择源服务器和目标服务器开始传输');
                addLogInfo('💡 提示: 传输过程中详细进度信息将不在日志中显示以保持最佳性能');
                addLogInfo(`⚡ 双击优化: 时间窗口${DOUBLE_CLICK_CONFIG.timeWindow}ms，响应更宽容友好`);
                addLogInfo('🎯 交互优化: 立即视觉反馈，无动画延迟，静态悬停效果');
                setupImageWheelZoom();
                bindPreviewAndEditorEvents();
                const CLIENT_IPV4 = (TURBOFILE_BOOT && Object.prototype.hasOwnProperty.call(TURBOFILE_BOOT, 'client_ipv4'))
                    ? TURBOFILE_BOOT.client_ipv4
                    : null;
                window.CLIENT_IPV4 = CLIENT_IPV4;
                if (CLIENT_IPV4) { addLogInfo('🖥️ 访问设备 IPv4: ' + CLIENT_IPV4); }
                if (CLIENT_IPV4) {
                    socket.emit('register_client', { client_ip: CLIENT_IPV4 });
                }
            socket.on('connect', () => {
                socketId = socket.id;
                updateRunControls();
            });
            updateRunControls();
            socket.on('connect', () => {
                socketId = socket.id;
                updateRunControls();
            });
            updateRunControls();


            switchLogoDisplay('header');


            initializeResizers();
            bindContextMenus();
            setupDragAndDrop();


            const sourceContainer = document.getElementById('sourceFileBrowser');
            if (sourceContainer) {
                sourceContainer.addEventListener('scroll', () => handleScrollLoadMore(true), { passive: true });
            }
            const targetContainer = document.getElementById('targetFileBrowser');
            if (targetContainer) {
                targetContainer.addEventListener('scroll', () => handleScrollLoadMore(false), { passive: true });
            }


            document.getElementById('sourceServer').addEventListener('change', async function() {
                if (this.value) {
                    const isWindows = isWindowsServer(this.value);


                    const defaultPath = getDefaultPathWithRemember(this.value, true);
                    if (!defaultPath) {
                        addLogWarning('⚠️ 未配置默认路径，请检查配置文件');
                        return;
                    }
                    currentSourcePath = defaultPath;
                    browseSourceInstant(currentSourcePath);


                    if (isWindows) {
                        loadWindowsDrives(this.value, true);
                        addLogInfo('💡 检测到Windows服务器，正在加载磁盘列表...');
                    } else {
                        hideWindowsDriveSelector(true);
                    }
                }
            });

            document.getElementById('targetServer').addEventListener('change', async function() {
                if (this.value) {
                    const isWindows = isWindowsServer(this.value);


                    const defaultPath = getDefaultPathWithRemember(this.value, false);
                    if (!defaultPath) {
                        addLogWarning('⚠️ 未配置默认路径，请检查配置文件');
                        return;
                    }
                    currentTargetPath = defaultPath;
                    browseTargetInstant(currentTargetPath);


                    if (isWindows) {
                        loadWindowsDrives(this.value, false);
                        addLogInfo('💡 检测到Windows服务器，正在加载磁盘列表...');
                    } else {
                        hideWindowsDriveSelector(false);
                    }
                }
            });


                applyRememberedSelections();


        });

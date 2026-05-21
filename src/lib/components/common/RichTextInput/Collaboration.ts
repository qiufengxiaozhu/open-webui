import type { Socket } from 'socket.io-client';
import type { SessionUser } from '$lib/stores';
import { Editor, Extension } from '@tiptap/core';

export type EditorContentGetter = () => {
	md: string;
	html: string;
	json: string;
};

// Custom Yjs Socket.IO provider
export class SocketIOCollaborationProvider {
	private editor: Editor | null = null;
	private editorContentGetter: EditorContentGetter | null = null;

	constructor(
		private readonly documentId: string,
		private readonly socket: Socket,
		private readonly user: SessionUser,
		private readonly initialContent: string | null = null
	) {}

	public getEditorExtension() {
		return Extension.create({
			name: 'yjsCollaboration',

			addProseMirrorPlugins: () => []
		});
	}

	public setEditor(editor: Editor, editorContentGetter: EditorContentGetter) {
		this.editor = editor;
		this.editorContentGetter = editorContentGetter;
	}

	public destroy() {
		if (this.socket?.connected) {
			this.socket.emit('ydoc:document:leave', {
				document_id: this.documentId,
				user_id: this.user?.id
			});
		}

		this.editor = null;
		this.editorContentGetter = null;
	}
}

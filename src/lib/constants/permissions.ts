export const DEFAULT_PERMISSIONS = {
	workspace: {
		models: false,
		prompts: false,
		tools: false,
		skills: false,
		models_import: false,
		models_export: false,
		prompts_import: false,
		prompts_export: false,
		tools_import: false,
		tools_export: false
	},
	sharing: {
		models: false,
		public_models: false,
		prompts: false,
		public_prompts: false,
		tools: false,
		public_tools: false,
		skills: false,
		public_skills: false,
		public_chats: false
	},
	access_grants: {
		allow_users: true
	},
	chat: {
		controls: true,
		valves: true,
		system_prompt: true,
		params: true,
		file_upload: true,
		web_upload: true,
		delete: true,
		delete_message: true,
		continue_response: true,
		regenerate_response: true,
		rate_response: true,
		edit: true,
		share: true,
		export: true,
		multiple_models: true,
		temporary: true,
		temporary_enforced: false
	},
	features: {
		api_keys: false,
		folders: true,
		direct_tool_servers: false,
		web_search: true,
		code_interpreter: true
	},
	settings: {
		interface: true
	}
} as const;

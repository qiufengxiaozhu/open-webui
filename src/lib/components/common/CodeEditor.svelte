<script lang="ts">
	import { onMount, getContext, tick } from 'svelte';

	import { formatPythonCode } from '$lib/apis/utils';
	import { toast } from 'svelte-sonner';
	import { user } from '$lib/stores';

	const i18n = getContext('i18n');

	export let boilerplate = '';
	export let value = '';

	export let onSave = () => {};
	export let onChange = () => {};

	let _value = '';
	let textareaEl: HTMLTextAreaElement | null = null;

	$: if (value !== undefined && value !== _value) {
		updateValue();
	}

	const updateValue = async () => {
		if (_value !== value) {
			_value = value;
			await tick();
			autoResize();
		}
	};

	export let id = '';
	export let lang = '';

	export const focus = () => {
		textareaEl?.focus();
	};

	const autoResize = () => {
		if (textareaEl) {
			textareaEl.style.height = 'auto';
			const maxH = window.innerHeight * 0.8;
			const targetH = Math.min(textareaEl.scrollHeight, maxH);
			textareaEl.style.height = targetH + 'px';
		}
	};

	export const formatPythonCodeHandler = async () => {
		if (!textareaEl) {
			return false;
		}

		if ($user?.role !== 'admin') {
			toast.error($i18n.t('Code formatting is not available on this platform'));
			return false;
		}

		const res = await formatPythonCode(localStorage.token, _value).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res && res.code) {
			_value = res.code;
			value = res.code;
			onChange(_value);
			await tick();

			toast.success($i18n.t('Code formatted successfully'));
			return true;
		}

		return false;
	};

	onMount(async () => {
		if (value === '') {
			value = boilerplate;
		}

		_value = value;
		await tick();
		autoResize();

		const keydownHandler = async (e: KeyboardEvent) => {
			if ((e.ctrlKey || e.metaKey) && e.key === 's') {
				e.preventDefault();
				onSave();
			}

			// Format code when Ctrl + Shift + F is pressed
			if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'f') {
				e.preventDefault();
				if (document.activeElement === textareaEl) {
					await formatPythonCodeHandler();
				}
			}
		};

		document.addEventListener('keydown', keydownHandler);

		return () => {
			document.removeEventListener('keydown', keydownHandler);
		};
	});
</script>

<textarea
	bind:this={textareaEl}
	id="code-textarea-{id}"
	class="w-full text-sm font-mono bg-transparent resize-none outline-none p-2 dark:bg-black dark:text-white"
	style="min-height: 3rem; max-height: 80vh; overflow-y: auto;"
	bind:value={_value}
	on:input={() => {
		value = _value;
		onChange(_value);
		autoResize();
	}}
	placeholder={$i18n.t('Enter your code here...')}
	spellcheck="false"
	data-lang={lang}
/>

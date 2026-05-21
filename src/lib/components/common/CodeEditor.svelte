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

	const updateValue = () => {
		if (_value !== value) {
			_value = value;
		}
	};

	export let id = '';
	export let lang = '';

	export const focus = () => {
		textareaEl?.focus();
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

	onMount(() => {
		if (value === '') {
			value = boilerplate;
		}

		_value = value;

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
	class="h-full w-full text-sm font-mono bg-transparent resize-none outline-none p-2 dark:bg-black dark:text-white"
	bind:value={_value}
	on:input={() => {
		value = _value;
		onChange(_value);
	}}
	placeholder={$i18n.t('Enter your code here...')}
	spellcheck="false"
	data-lang={lang}
/>

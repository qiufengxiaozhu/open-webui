<script lang="ts">
	import { getContext } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';

	const i18n = getContext('i18n');

	export let direct = false;
	export let edit = false;
	export let show = false;
	export let connection = { url: '', key: '', name: '', path: '/openapi.json', enabled: false };
	export let onSubmit: (connection: typeof connection) => void = () => {};
	export let onDelete: () => void = () => {};
</script>

<Modal bind:show size="sm">
	<div class="px-5 py-4 text-sm text-gray-500 dark:text-gray-400">
		{$i18n.t('Terminal server configuration is not available on this platform.')}
	</div>
</Modal>

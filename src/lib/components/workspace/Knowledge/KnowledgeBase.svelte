<script lang="ts">
	import { getContext } from 'svelte';

	const i18n = getContext('i18n');
</script>

<div class="flex h-full w-full items-center justify-center p-8 text-sm text-gray-500 dark:text-gray-400">
	{$i18n.t('Knowledge base management is not available on this platform.')}
</div>

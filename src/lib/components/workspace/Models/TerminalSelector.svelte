<script lang="ts">
	import { getContext, onMount } from 'svelte';

	const i18n = getContext('i18n');

	export let terminalId: string = '';

	let terminals: { id: string; name?: string }[] = [];

	onMount(async () => {
		terminals = [];
	});
</script>

{#if terminals.length > 0}
	<div class="flex w-full justify-between mb-1">
		<div class="self-center text-xs font-medium text-gray-500">{$i18n.t('Terminal')}</div>
	</div>

	<select
		class="w-full text-sm bg-transparent outline-hidden cursor-pointer"
		bind:value={terminalId}
	>
		<option value="">{$i18n.t('None')}</option>
		{#each terminals as terminal (terminal.id)}
			<option value={terminal.id}>{terminal.name || terminal.id}</option>
		{/each}
	</select>
{/if}

<script lang="ts">
	import { createEventDispatcher } from 'svelte';

	export let onExport: (() => void) | null = null;

	const dispatch = createEventDispatcher();
</script>

<div class="flex items-center gap-1">
	{#if onExport}
		<button
			class="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
			on:click={onExport}
			type="button"
		>
			Export
		</button>
	{/if}
	<button
		class="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition text-red-500"
		on:click={() => dispatch('delete')}
		type="button"
	>
		Delete
	</button>
</div>
